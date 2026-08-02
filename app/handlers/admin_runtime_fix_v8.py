from datetime import datetime, timezone
from html import escape

from aiogram import F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import get_settings
from app.db.session import SessionFactory
from app.services.app_settings import (
    CARD_PAYMENT_TEXT_KEY,
    STARS_RUB_PER_STAR_KEY,
    STATS_RESET_AT_KEY,
    get_bazaar_chat_id,
    get_bazaar_url,
    get_card_payment_text,
    get_staff_chat_id,
    get_stars_rub_per_star,
    set_setting,
)
from app.services.ui_screen import delete_user_input, render_user_screen
from app.services.users import is_admin
from app.states import AdminFlow

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


async def allowed_message(message: Message, *, owner: bool = False) -> bool:
    if not message.from_user or message.chat.type != ChatType.PRIVATE:
        return False
    if owner and message.from_user.id != settings.owner_id:
        return False
    async with SessionFactory() as session:
        return await is_admin(session, message.from_user.id)


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
                InlineKeyboardButton(
                    text="💳 Текст оплаты картой",
                    callback_data="settingsv9:card_text",
                    style="primary",
                ),
                InlineKeyboardButton(
                    text="⭐ Курс Stars",
                    callback_data="settingsv9:stars_rate",
                    style="primary",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👁 Предпросмотр карты",
                    callback_data="settingsv9:card_preview",
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
        stars_rate = await get_stars_rub_per_star(session)
    return (
        "<b><u>⚙️ НАСТРОЙКИ</u></b>\n\n"
        f"├ Рабочая группа: <code>{bazaar_id}</code>\n"
        f"├ Ссылка: <code>{escape(bazaar_url)}</code>\n"
        f"├ Группа стафа: <code>{staff_id}</code>\n"
        f"└ Курс Stars: <b>1 ⭐ = {stars_rate} ₽</b>\n\n"
        "<i>Полный текст карточки «Карта» редактируется отдельной кнопкой. "
        "Доступны подстановки: {order_id}, {amount}, {kind}, {user_id}.</i>"
    )


async def render(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    await render_user_screen(
        callback.bot,
        callback.from_user.id,
        text,
        markup or settings_markup(),
        source_message=callback.message,
        media_key="admin",
        text_only=True,
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


@router.callback_query(F.data == "settingsv9:card_text")
async def card_text_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await allowed(callback, owner=True):
        return
    await state.set_state(AdminFlow.entering_card_payment_text)
    await render(
        callback,
        "<b>💳 Текст карточки «Карта»</b>\n\n"
        "Отправьте одним сообщением весь текст, который увидит покупатель. "
        "Можно использовать оформление Telegram и подстановки:\n\n"
        "<code>{order_id}</code> — номер заказа\n"
        "<code>{amount}</code> — сумма\n"
        "<code>{kind}</code> — этап оплаты\n"
        "<code>{user_id}</code> — ID покупателя",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data="adminv3:settings")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
        ]),
    )
    await callback.answer()


@router.message(AdminFlow.entering_card_payment_text, F.text)
async def card_text_save(message: Message, state: FSMContext) -> None:
    if not await allowed_message(message, owner=True):
        return
    value = message.html_text or escape(message.text)
    if len(value) > 3500:
        await render_user_screen(
            message.bot,
            message.from_user.id,
            "<b>❌ Текст слишком длинный</b>\n\nМаксимум — 3500 символов.",
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Настройки", callback_data="adminv3:settings")]
            ]),
            media_key="admin",
            text_only=True,
        )
        await delete_user_input(message)
        return
    async with SessionFactory() as session:
        await set_setting(session, CARD_PAYMENT_TEXT_KEY, value, message.from_user.id)
    await state.clear()
    await delete_user_input(message)
    await render_user_screen(
        message.bot,
        message.from_user.id,
        "<b>✅ Текст оплаты картой сохранён</b>",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👁 Посмотреть", callback_data="settingsv9:card_preview", style="success")],
            [InlineKeyboardButton(text="⬅️ Настройки", callback_data="adminv3:settings")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
        ]),
        media_key="admin",
        text_only=True,
    )


@router.callback_query(F.data == "settingsv9:card_preview")
async def card_preview(callback: CallbackQuery) -> None:
    if not await allowed(callback):
        return
    async with SessionFactory() as session:
        template = await get_card_payment_text(session)
    try:
        preview = template.format(order_id=123, amount=1500, kind="полная оплата", user_id=123456789)
    except (KeyError, ValueError):
        preview = template + "\n\n<b>⚠️ В тексте есть неизвестная подстановка.</b>"
    await render(
        callback,
        "<b><u>ПРЕДПРОСМОТР</u></b>\n\n" + preview,
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Изменить текст", callback_data="settingsv9:card_text", style="primary")],
            [InlineKeyboardButton(text="⬅️ Настройки", callback_data="adminv3:settings")],
        ]),
    )
    await callback.answer()


@router.callback_query(F.data == "settingsv9:stars_rate")
async def stars_rate_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await allowed(callback, owner=True):
        return
    await state.set_state(AdminFlow.entering_stars_rate)
    await render(
        callback,
        "<b>⭐ Курс Telegram Stars</b>\n\n"
        "Отправьте, сколько рублей соответствует одной звезде. Например: <code>2</code>. "
        "Количество звёзд округляется вверх.",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data="adminv3:settings")]
        ]),
    )
    await callback.answer()


@router.message(AdminFlow.entering_stars_rate, F.text)
async def stars_rate_save(message: Message, state: FSMContext) -> None:
    if not await allowed_message(message, owner=True):
        return
    try:
        value = int(message.text.strip())
        if not 1 <= value <= 1000:
            raise ValueError
    except ValueError:
        await render_user_screen(
            message.bot,
            message.from_user.id,
            "<b>❌ Нужна целая сумма от 1 до 1000</b>",
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Настройки", callback_data="adminv3:settings")]
            ]),
            media_key="admin",
            text_only=True,
        )
        await delete_user_input(message)
        return
    async with SessionFactory() as session:
        await set_setting(session, STARS_RUB_PER_STAR_KEY, str(value), message.from_user.id)
    await state.clear()
    await delete_user_input(message)
    await render_user_screen(
        message.bot,
        message.from_user.id,
        f"<b>✅ Курс сохранён</b>\n\n1 ⭐ = <b>{value} ₽</b>",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Готово", callback_data="adminv3:settings", style="success")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
        ]),
        media_key="admin",
        text_only=True,
    )


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
        text_only=True,
    )
    await callback.answer("Статистика обнулена", show_alert=True)