from datetime import datetime, timezone
from html import escape

from aiogram import F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from app.config import get_settings
from app.db.session import SessionFactory
from app.services.app_settings import (
    STATS_RESET_AT_KEY,
    get_bazaar_chat_id,
    get_bazaar_url,
    get_staff_chat_id,
    set_setting,
)
from app.services.ui_screen import render_user_screen
from app.services.users import is_admin

router = Router(name="admin_runtime_fix_v8")
settings = get_settings()


async def allowed(callback: CallbackQuery, *, owner: bool = False) -> bool:
    if callback.message and callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer("Раздел доступен только в личке.", show_alert=True)
        return False
    if owner and callback.from_user.id != settings.owner_id:
        await callback.answer("Доступно только владельцу.", show_alert=True)
        return False
    async with SessionFactory() as session:
        value = await is_admin(session, callback.from_user.id)
    if not value:
        await callback.answer("Доступ запрещён.", show_alert=True)
    return value


def settings_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🧪 Проверить стаф", callback_data="settingsv3:test_staff"),
                InlineKeyboardButton(text="✏️ ID стафа", callback_data="settingsv3:change_staff"),
            ],
            [
                InlineKeyboardButton(text="🧪 Проверить группу", callback_data="settingsv7:test_bazaar"),
                InlineKeyboardButton(text="✏️ ID группы", callback_data="settingsv7:change_bazaar"),
            ],
            [
                InlineKeyboardButton(
                    text="🔗 Изменить ссылку группы",
                    callback_data="settingsv7:change_bazaar_url",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(text="⬅️ Админ-панель", callback_data="profile:admin"),
                InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home"),
            ],
        ]
    )


async def settings_text() -> str:
    async with SessionFactory() as session:
        staff_id = await get_staff_chat_id(session)
        bazaar_id = await get_bazaar_chat_id(session)
        bazaar_url = await get_bazaar_url(session)
    return (
        "<b><u>⚙️ НАСТРОЙКИ ГРУПП</u></b>\n\n"
        f"├ Рабочая группа: <code>{bazaar_id}</code>\n"
        f"├ Ссылка: <code>{escape(bazaar_url)}</code>\n"
        f"└ Группа стафа: <code>{staff_id}</code>\n\n"
        "<i>Рабочую группу нельзя менять во время активной рекламы, чтобы не оставить закрепы и префиксы в старом чате.</i>"
    )


async def render(callback: CallbackQuery, text: str) -> None:
    await render_user_screen(
        callback.bot,
        callback.from_user.id,
        text,
        settings_markup(),
        source_message=callback.message,
        media_key="admin",
    )


async def diagnose(bot, chat_id: int, *, pin: bool) -> tuple[bool, str]:
    try:
        chat = await bot.get_chat(chat_id)
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id, me.id)
        status = getattr(member.status, "value", member.status)
        admin_status = status in {
            ChatMemberStatus.ADMINISTRATOR.value,
            ChatMemberStatus.CREATOR.value,
        }
        can_pin = bool(getattr(member, "can_pin_messages", False)) or status == ChatMemberStatus.CREATOR.value
        if not admin_status:
            return False, f"бот не администратор, статус: {status}"
        if pin and not can_pin:
            return False, "нет права закреплять сообщения"
        return True, f"{chat.title or chat_id}: всё работает"
    except Exception as error:
        return False, f"{type(error).__name__}: {error}"


@router.callback_query(F.data == "adminv3:settings")
async def settings_page(callback: CallbackQuery, state: FSMContext) -> None:
    if not await allowed(callback):
        return
    await state.clear()
    await render(callback, await settings_text())
    await callback.answer()


@router.callback_query(F.data == "settingsv3:test_staff")
async def test_staff(callback: CallbackQuery) -> None:
    if not await allowed(callback):
        return
    async with SessionFactory() as session:
        chat_id = await get_staff_chat_id(session)
    ok, detail = await diagnose(callback.bot, chat_id, pin=False)
    await render(
        callback,
        await settings_text()
        + f"\n\n<b>{'✅ Стаф работает' if ok else '❌ Ошибка стафа'}</b>\n<code>{escape(detail)}</code>",
    )
    await callback.answer("Проверено")


@router.callback_query(F.data == "settingsv7:test_bazaar")
async def test_bazaar(callback: CallbackQuery) -> None:
    if not await allowed(callback):
        return
    async with SessionFactory() as session:
        chat_id = await get_bazaar_chat_id(session)
    ok, detail = await diagnose(callback.bot, chat_id, pin=True)
    await render(
        callback,
        await settings_text()
        + f"\n\n<b>{'✅ Рабочая группа готова' if ok else '❌ Ошибка рабочей группы'}</b>\n<code>{escape(detail)}</code>",
    )
    await callback.answer("Проверено")


@router.callback_query(F.data == "statsv7:clear_do")
async def stats_clear(callback: CallbackQuery) -> None:
    if not await allowed(callback, owner=True):
        return
    async with SessionFactory() as session:
        await set_setting(
            session,
            STATS_RESET_AT_KEY,
            datetime.now(timezone.utc).isoformat(),
            callback.from_user.id,
        )
    await render_user_screen(
        callback.bot,
        callback.from_user.id,
        "<b>✅ Статистика обнулена</b>\n\n"
        "Клиенты, заказы и платежи сохранены. Новый статистический период начинается сейчас.",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📊 Открыть статистику", callback_data="adminv3:stats", style="success")],
                [InlineKeyboardButton(text="⬅️ Админ-панель", callback_data="profile:admin")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
            ]
        ),
        source_message=callback.message,
        media_key="admin",
    )
    await callback.answer("Статистика обнулена", show_alert=True)
