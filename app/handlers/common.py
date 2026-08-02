from datetime import datetime, timezone
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from app.config import get_settings
from app.db.session import SessionFactory
from app.keyboards import membership_keyboard, phone_keyboard, profile_keyboard
from app.models import User
from app.services.blocking import get_user_block
from app.services.ui_screen import delete_user_input, render_user_screen
from app.services.users import inspect_membership, is_admin, upsert_user

router = Router(name="common")
settings = get_settings()


def profile_caption(user: User, blocked_reason: str | None = None) -> str:
    full_name = " ".join(part for part in [user.first_name, user.last_name] if part).strip()
    username = f"@{escape(user.username)}" if user.username else "не указан"
    phone = escape(user.phone or "не указан")
    access_line = (
        f"Доступ: <b>🚫 ограничен</b>\nПричина: <i>{escape(blocked_reason)}</i>"
        if blocked_reason
        else "Доступ: <b>✅ подтверждён</b>"
    )
    return (
        "<b><u>👤 ГЛАВНОЕ МЕНЮ</u></b>\n\n"
        f"Имя: <b>{escape(full_name or 'Без имени')}</b>\n"
        f"Username: {username}\n"
        f"ID: <code>{user.id}</code>\n"
        f"Телефон: <code>{phone}</code>\n"
        f"{access_line}\n\n"
        "<i>Все разделы открываются в этом сообщении. Новые меню бот больше не создаёт.</i>"
    )


async def show_profile(
    bot: Bot,
    chat_id: int,
    user: User,
    admin: bool,
    *,
    source_message: Message | None = None,
) -> None:
    async with SessionFactory() as session:
        block = await get_user_block(session, user.id)
    await render_user_screen(
        bot,
        user.id,
        profile_caption(user, block.reason if block else None),
        profile_keyboard(admin, blocked=bool(block and not admin)),
        source_message=source_message,
        media_key="main",
    )


async def show_access_result(
    bot: Bot,
    chat_id: int,
    user: User,
    admin: bool,
    *,
    source_message: Message | None = None,
) -> None:
    if admin:
        await show_profile(bot, chat_id, user, True, source_message=source_message)
        return

    result, status, error = await inspect_membership(bot, user.id)
    async with SessionFactory() as session:
        stored = await session.get(User, user.id)
        if stored:
            stored.is_bazaar_member = result == "member"
            stored.bazaar_status = status
            stored.last_seen_at = datetime.now(timezone.utc)
            await session.commit()

    if result == "member":
        user.is_bazaar_member = True
        user.bazaar_status = status
        await show_profile(bot, chat_id, user, False, source_message=source_message)
    elif result == "not_member":
        await render_user_screen(
            bot,
            user.id,
            "<b>❌ Для покупки рекламы нужно вступить в группу</b>\n\n"
            "После вступления нажмите «Проверить снова».",
            membership_keyboard(settings.bazaar_url),
            source_message=source_message,
            media_key="main",
        )
    else:
        await render_user_screen(
            bot,
            user.id,
            "<b>⚠️ Не удалось проверить участие</b>\n\nНажмите «Проверить снова».",
            membership_keyboard(settings.bazaar_url),
            source_message=source_message,
            media_key="main",
        )
        if error:
            try:
                await bot.send_message(
                    settings.owner_id,
                    f"⚠️ Ошибка проверки участника <code>{user.id}</code>:\n<code>{escape(error)}</code>",
                )
            except Exception:
                pass


@router.message(CommandStart())
async def start(message: Message, bot: Bot) -> None:
    if not message.from_user:
        return
    async with SessionFactory() as session:
        user = await upsert_user(session, bot, message.from_user)
        admin = await is_admin(session, user.id)

    if admin:
        await show_profile(bot, message.chat.id, user, True)
        await delete_user_input(message)
        return

    if not user.phone:
        await message.answer(
            "📱 Отправьте номер телефона для продолжения.",
            reply_markup=phone_keyboard(),
        )
        return

    await show_access_result(bot, message.chat.id, user, False)
    await delete_user_input(message)


@router.message(F.contact)
async def save_contact(message: Message, bot: Bot) -> None:
    if not message.from_user or not message.contact:
        return
    if message.contact.user_id not in (None, message.from_user.id):
        await message.answer("Отправьте собственный номер кнопкой ниже.", reply_markup=phone_keyboard())
        return

    async with SessionFactory() as session:
        user = await session.get(User, message.from_user.id)
        if user is None:
            user = await upsert_user(session, bot, message.from_user)
        user.phone = message.contact.phone_number
        user.last_seen_at = datetime.now(timezone.utc)
        await session.commit()
        admin = await is_admin(session, user.id)

    await delete_user_input(message)
    await show_access_result(bot, message.chat.id, user, admin)


@router.callback_query(F.data == "profile:recheck")
async def recheck_membership(callback: CallbackQuery, bot: Bot) -> None:
    async with SessionFactory() as session:
        user = await session.get(User, callback.from_user.id)
        admin = await is_admin(session, callback.from_user.id)
    if not user:
        await callback.answer("Откройте бота заново.", show_alert=True)
        return
    await show_access_result(
        bot,
        callback.from_user.id,
        user,
        admin,
        source_message=callback.message,
    )
    await callback.answer("Проверено")


@router.callback_query(F.data.in_({"profile:home", "nav:home", "order:cancel"}))
async def return_profile(
    callback: CallbackQuery,
    bot: Bot,
    state: FSMContext,
) -> None:
    await state.clear()
    async with SessionFactory() as session:
        user = await session.get(User, callback.from_user.id)
        admin = await is_admin(session, callback.from_user.id)
    if not user:
        await callback.answer("Откройте бота заново.", show_alert=True)
        return
    await show_profile(
        bot,
        callback.from_user.id,
        user,
        admin,
        source_message=callback.message,
    )
    await callback.answer("Главное меню")


@router.callback_query(F.data == "blocked:info")
async def blocked_info(callback: CallbackQuery) -> None:
    async with SessionFactory() as session:
        block = await get_user_block(session, callback.from_user.id)
    await callback.answer(
        block.reason if block else "Доступ уже восстановлен.",
        show_alert=True,
    )
