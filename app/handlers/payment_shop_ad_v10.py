from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from app.db.session import SessionFactory
from app.enums import OrderStatus
from app.handlers.admin_runtime_fix_v8 import allowed, allowed_message, settings_markup, settings_text
from app.models import AdOrder
from app.services.app_settings import (
    STARS_SHOP_TEXT_KEY,
    STARS_SHOP_URL_KEY,
    get_stars_shop_text,
    get_stars_shop_url,
    set_setting,
)
from app.services.orders import deposit_amount
from app.services.ui_screen import delete_user_input, render_user_screen
from app.states import AdminFlow

router = Router(name="payment_shop_ad_v10")
PAYABLE_STATUSES = {
    OrderStatus.AWAITING_PAYMENT.value,
    OrderStatus.AWAITING_DEPOSIT.value,
    OrderStatus.BOOKED.value,
}


def payment_amount(order: AdOrder, kind: str) -> int:
    if kind == "deposit":
        return max(0, deposit_amount(order.price_rub) - order.paid_rub)
    if kind in {"full", "remainder"}:
        return max(0, order.price_rub - order.paid_rub)
    raise ValueError("Неизвестный этап оплаты")


def payment_kind_name(kind: str) -> str:
    return {
        "deposit": "предоплата",
        "remainder": "остаток",
        "full": "полная оплата",
    }.get(kind, kind)


def payment_keyboard(
    order_id: int,
    kind: str,
    shop_url: str | None,
) -> InlineKeyboardMarkup:
    shop_button = (
        InlineKeyboardButton(
            text="🛒 Купить Stars у нас",
            url=shop_url,
            style="primary",
        )
        if shop_url
        else InlineKeyboardButton(
            text="🛒 Купить Stars у нас · скоро",
            callback_data="payv10:shop_soon",
        )
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⭐ Оплатить Stars",
                    callback_data=f"payv9:stars:{order_id}:{kind}",
                    style="success",
                ),
                InlineKeyboardButton(
                    text="💳 Оплатить картой",
                    callback_data=f"payv9:card:{order_id}:{kind}",
                    style="primary",
                ),
            ],
            [shop_button],
            [InlineKeyboardButton(text="⬅️ К заказу", callback_data=f"buyerorder:view:{order_id}")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
        ]
    )


@router.callback_query(F.data.startswith("payv9:choose:"))
async def choose_payment_with_shop(callback: CallbackQuery) -> None:
    _, _, raw_id, kind = callback.data.split(":", 3)
    order_id = int(raw_id)
    async with SessionFactory() as session:
        order = await session.scalar(
            select(AdOrder).where(
                AdOrder.id == order_id,
                AdOrder.user_id == callback.from_user.id,
            )
        )
        shop_url = await get_stars_shop_url(session)
        shop_text = await get_stars_shop_text(session)

    if not order or order.status not in PAYABLE_STATUSES:
        await callback.answer("Этот заказ сейчас нельзя оплатить.", show_alert=True)
        return
    amount = payment_amount(order, kind)
    if amount <= 0:
        await callback.answer("Этот этап уже оплачен.", show_alert=True)
        return

    await render_user_screen(
        callback.bot,
        callback.from_user.id,
        f"<b><u>💰 ОПЛАТА ЗАКАЗА №{order.id}</u></b>\n\n"
        f"├ Этап: <b>{payment_kind_name(kind)}</b>\n"
        f"└ Сумма: <b>{amount} ₽</b>\n\n"
        "Выберите способ оплаты. Повторное нажатие не создаст двойное начисление.\n\n"
        f"{shop_text}",
        payment_keyboard(order.id, kind, shop_url),
        source_message=callback.message,
        media_key="payment",
        text_only=True,
    )
    await callback.answer()


@router.callback_query(F.data == "payv10:shop_soon")
async def shop_soon(callback: CallbackQuery) -> None:
    await callback.answer(
        "Отдельный бот покупки Stars ещё готовится. Ссылка появится здесь автоматически после настройки.",
        show_alert=True,
    )


def expanded_settings_markup() -> InlineKeyboardMarkup:
    base = settings_markup().inline_keyboard
    return InlineKeyboardMarkup(
        inline_keyboard=[
            *base[:-1],
            [
                InlineKeyboardButton(
                    text="🛒 Реклама магазина Stars",
                    callback_data="settingsv10:stars_shop",
                    style="primary",
                )
            ],
            base[-1],
        ]
    )


async def shop_status_text() -> str:
    async with SessionFactory() as session:
        url = await get_stars_shop_url(session)
        text = await get_stars_shop_text(session)
    return (
        "\n\n<b>Магазин Stars:</b> "
        + (f"<code>{escape(url)}</code>" if url else "<i>ссылка пока не настроена</i>")
        + f"\n<b>Рекламный текст:</b>\n{text}"
    )


@router.callback_query(F.data == "adminv3:settings")
async def settings_with_stars_shop(callback: CallbackQuery, state: FSMContext) -> None:
    if not await allowed(callback):
        return
    await state.clear()
    await render_user_screen(
        callback.bot,
        callback.from_user.id,
        await settings_text() + await shop_status_text(),
        expanded_settings_markup(),
        source_message=callback.message,
        media_key="admin",
        text_only=True,
    )
    await callback.answer()


@router.callback_query(F.data == "settingsv10:stars_shop")
async def stars_shop_settings(callback: CallbackQuery, state: FSMContext) -> None:
    if not await allowed(callback, owner=True):
        return
    await state.clear()
    await render_user_screen(
        callback.bot,
        callback.from_user.id,
        "<b><u>🛒 РЕКЛАМА МАГАЗИНА STARS</u></b>"
        + await shop_status_text()
        + "\n\nПосле создания отдельного бота укажите здесь его @username или полную ссылку.",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔗 Изменить ссылку",
                        callback_data="settingsv10:stars_shop_url",
                        style="primary",
                    ),
                    InlineKeyboardButton(
                        text="📝 Изменить текст",
                        callback_data="settingsv10:stars_shop_text",
                        style="primary",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="❌ Отключить ссылку",
                        callback_data="settingsv10:stars_shop_disable",
                        style="danger",
                    )
                ],
                [InlineKeyboardButton(text="⬅️ Настройки", callback_data="adminv3:settings")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
            ]
        ),
        source_message=callback.message,
        media_key="admin",
        text_only=True,
    )
    await callback.answer()


@router.callback_query(F.data == "settingsv10:stars_shop_url")
async def stars_shop_url_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await allowed(callback, owner=True):
        return
    await state.set_state(AdminFlow.entering_stars_shop_url)
    await render_user_screen(
        callback.bot,
        callback.from_user.id,
        "<b>🔗 Ссылка на отдельный бот Stars</b>\n\n"
        "Отправьте <code>@username</code>, <code>t.me/username</code> или полную HTTPS-ссылку.",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Отмена", callback_data="settingsv10:stars_shop")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
            ]
        ),
        source_message=callback.message,
        media_key="admin",
        text_only=True,
    )
    await callback.answer()


def normalize_shop_url(value: str) -> str | None:
    raw = value.strip()
    if raw.startswith("@") and len(raw) > 1:
        return f"https://t.me/{raw[1:]}"
    if raw.startswith("t.me/"):
        return "https://" + raw
    if raw.startswith("https://"):
        return raw
    return None


@router.message(AdminFlow.entering_stars_shop_url, F.text)
async def stars_shop_url_save(message: Message, state: FSMContext) -> None:
    if not await allowed_message(message, owner=True):
        return
    url = normalize_shop_url(message.text)
    if not url:
        await render_user_screen(
            message.bot,
            message.from_user.id,
            "<b>❌ Ссылка не распознана</b>\n\n"
            "Отправьте @username, t.me/username или ссылку, начинающуюся с https://.",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Отмена", callback_data="settingsv10:stars_shop")]
                ]
            ),
            media_key="admin",
            text_only=True,
        )
        await delete_user_input(message)
        return
    async with SessionFactory() as session:
        await set_setting(session, STARS_SHOP_URL_KEY, url, message.from_user.id)
    await state.clear()
    await delete_user_input(message)
    await render_user_screen(
        message.bot,
        message.from_user.id,
        f"<b>✅ Ссылка магазина Stars сохранена</b>\n\n<code>{escape(url)}</code>",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Готово", callback_data="settingsv10:stars_shop", style="success")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
            ]
        ),
        media_key="admin",
        text_only=True,
    )


@router.callback_query(F.data == "settingsv10:stars_shop_text")
async def stars_shop_text_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await allowed(callback, owner=True):
        return
    await state.set_state(AdminFlow.entering_stars_shop_text)
    await render_user_screen(
        callback.bot,
        callback.from_user.id,
        "<b>📝 Рекламный текст магазина Stars</b>\n\n"
        "Отправьте короткий текст, который будет показан в выборе способа оплаты.",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Отмена", callback_data="settingsv10:stars_shop")]
            ]
        ),
        source_message=callback.message,
        media_key="admin",
        text_only=True,
    )
    await callback.answer()


@router.message(AdminFlow.entering_stars_shop_text, F.text)
async def stars_shop_text_save(message: Message, state: FSMContext) -> None:
    if not await allowed_message(message, owner=True):
        return
    text = message.html_text or escape(message.text)
    if len(text) > 800:
        await render_user_screen(
            message.bot,
            message.from_user.id,
            "<b>❌ Слишком длинный текст</b>\n\nМаксимум — 800 символов.",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Отмена", callback_data="settingsv10:stars_shop")]
                ]
            ),
            media_key="admin",
            text_only=True,
        )
        await delete_user_input(message)
        return
    async with SessionFactory() as session:
        await set_setting(session, STARS_SHOP_TEXT_KEY, text, message.from_user.id)
    await state.clear()
    await delete_user_input(message)
    await render_user_screen(
        message.bot,
        message.from_user.id,
        "<b>✅ Рекламный текст сохранён</b>\n\n" + text,
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Готово", callback_data="settingsv10:stars_shop", style="success")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
            ]
        ),
        media_key="admin",
        text_only=True,
    )


@router.callback_query(F.data == "settingsv10:stars_shop_disable")
async def stars_shop_disable(callback: CallbackQuery) -> None:
    if not await allowed(callback, owner=True):
        return
    async with SessionFactory() as session:
        await set_setting(session, STARS_SHOP_URL_KEY, "", callback.from_user.id)
    await callback.answer("Ссылка отключена", show_alert=True)
    await stars_shop_settings(callback, FSMContext(storage=None, key=None))
