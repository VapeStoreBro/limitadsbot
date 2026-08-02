from datetime import datetime, timezone
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, FSInputFile, Message, ReplyKeyboardRemove

from app.config import get_settings
from app.db.session import SessionFactory
from app.keyboards import membership_keyboard, phone_keyboard, profile_keyboard
from app.models import User
from app.services.price_card import ensure_main_menu_card
from app.services.users import inspect_membership, is_admin, upsert_user

router = Router(name="common")
settings = get_settings()


def profile_caption(user: User) -> str:
    full_name = " ".join(part for part in [user.first_name, user.last_name] if part).strip()
    username = f"@{escape(user.username)}" if user.username else "не указан"
    phone = escape(user.phone or "не указан")
    return (
        "<b>👤 Профиль</b>\n\n"
        f"Имя: <b>{escape(full_name or 'Без имени')}</b>\n"
        f"Username: {username}\n"
        f"ID: <code>{user.id}</code>\n"
        f"Телефон: <code>{phone}</code>\n"
        "Доступ: <b>✅ подтверждён</b>"
    )


async def show_profile(bot: Bot, chat_id: int, user: User, admin: bool) -> None:
    caption = profile_caption(user)
    keyboard = profile_keyboard(admin)
    menu_image = ensure_main_menu_card()
    if menu_image:
        await bot.send_photo(
            chat_id,
            FSInputFile(menu_image),
            caption=caption,
            reply_markup=keyboard,
        )
        return
    await bot.send_message(chat_id, caption, reply_markup=keyboard)


async def show_access_result(bot: Bot, chat_id: int, user: User, admin: bool) -> None:
    if admin:
        await show_profile(bot, chat_id, user, True)
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
        await show_profile(bot, chat_id, user, False)
    elif result == "not_member":
        await bot.send_message(
            chat_id,
            "❌ Для покупки рекламы нужно вступить в группу.",
            reply_markup=membership_keyboard(settings.bazaar_url),
        )
    else:
        await bot.send_message(
            chat_id,
            "⚠️ Не удалось проверить участие. Нажмите кнопку ещё раз.",
            reply_markup=membership_keyboard(settings.bazaar_url),
        )
        if error:
            await bot.send_message(
                settings.staff_chat_id,
                f"⚠️ Ошибка проверки участника <code>{user.id}</code>:\n<code>{escape(error)}</code>",
            )


@router.message(CommandStart())
async def start(message: Message, bot: Bot) -> None:
    if not message.from_user:
        return
    async with SessionFactory() as session:
        user = await upsert_user(session, bot, message.from_user)
        admin = await is_admin(session, user.id)

    if admin:
        await message.answer("✅ Вход администратора", reply_markup=ReplyKeyboardRemove())
        await show_profile(bot, message.chat.id, user, True)
        return

    if not user.phone:
        await message.answer(
            "📱 Отправьте номер телефона для продолжения.",
            reply_markup=phone_keyboard(),
        )
        return

    await message.answer("Проверяю доступ…", reply_markup=ReplyKeyboardRemove())
    await show_access_result(bot, message.chat.id, user, False)


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

    await message.answer("✅", reply_markup=ReplyKeyboardRemove())
    await show_access_result(bot, message.chat.id, user, admin)


@router.callback_query(F.data == "profile:recheck")
async def recheck_membership(callback: CallbackQuery, bot: Bot) -> None:
    async with SessionFactory() as session:
        user = await session.get(User, callback.from_user.id)
        admin = await is_admin(session, callback.from_user.id)
    if not user:
        await callback.answer("Нажмите /start", show_alert=True)
        return
    await callback.answer("Проверяю…")
    await show_access_result(bot, callback.from_user.id, user, admin)


@router.callback_query(F.data == "profile:home")
async def return_profile(callback: CallbackQuery, bot: Bot) -> None:
    async with SessionFactory() as session:
        user = await session.get(User, callback.from_user.id)
        admin = await is_admin(session, callback.from_user.id)
    if not user:
        await callback.answer("Нажмите /start", show_alert=True)
        return
    await callback.answer()
    await show_profile(bot, callback.from_user.id, user, admin)
