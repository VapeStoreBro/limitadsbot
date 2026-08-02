from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from app.db.session import SessionFactory
from app.enums import OrderStatus
from app.keyboards import TARIFF_NAMES
from app.models import AdOrder
from app.services.users import is_admin

router = Router(name="admin_orders_v4")

STATUS_NAMES = {
    OrderStatus.MODERATION.value: "На модерации",
    OrderStatus.AWAITING_PAYMENT.value: "Ждёт оплату",
    OrderStatus.AWAITING_DEPOSIT.value: "Ждёт предоплату",
    OrderStatus.BOOKED.value: "Забронирован",
    OrderStatus.READY.value: "Готов к запуску",
}


async def allowed(callback: CallbackQuery) -> bool:
    if callback.message and callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer("Раздел доступен только в личке бота.", show_alert=True)
        return False
    async with SessionFactory() as session:
        result = await is_admin(session, callback.from_user.id)
    if not result:
        await callback.answer("Доступ запрещён.", show_alert=True)
    return result


async def send_list(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    statuses = list(STATUS_NAMES)
    async with SessionFactory() as session:
        orders = (
            await session.scalars(
                select(AdOrder)
                .where(AdOrder.status.in_(statuses))
                .order_by(AdOrder.id.desc())
                .limit(30)
            )
        ).all()
    rows = [
        [
            InlineKeyboardButton(
                text=(
                    f"№{order.id} · {TARIFF_NAMES.get(order.tariff_code, order.tariff_code)} · "
                    f"{STATUS_NAMES.get(order.status, order.status)}"
                ),
                callback_data=f"adminorder:view:{order.id}",
            )
        ]
        for order in orders
    ]
    rows.append(
        [
            InlineKeyboardButton(text="⬅️ Админ-панель", callback_data="profile:admin"),
            InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home"),
        ]
    )
    await callback.answer()
    await callback.bot.send_message(
        callback.from_user.id,
        "<b><u>📥 ЗАЯВКИ И ЗАКАЗЫ</u></b>\n\n"
        + (
            "Выберите заказ, чтобы посмотреть пост и доступные действия."
            if orders
            else "Сейчас нет заказов, требующих действий."
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.in_({"adminv3:orders", "adminorder:list"}))
async def orders(callback: CallbackQuery, state: FSMContext) -> None:
    if not await allowed(callback):
        return
    await send_list(callback, state)
