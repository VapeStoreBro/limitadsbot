from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery

from app.config import get_settings
from app.db.session import SessionFactory
from app.enums import OrderStatus
from app.keyboards_v3 import private_activation_keyboard
from app.models import AdOrder, Payment
from app.services.orders import deposit_amount

router = Router(name="payments_v3")
settings = get_settings()


@router.callback_query(F.data.startswith("testpay:"))
async def test_payment_v3(callback: CallbackQuery, bot: Bot) -> None:
    _, raw_id, kind = callback.data.split(":", 2)
    order_id = int(raw_id)
    now = datetime.now(timezone.utc)

    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
        if not order or order.user_id != callback.from_user.id:
            await callback.answer("Заказ не найден.", show_alert=True)
            return

        if kind == "deposit":
            amount = deposit_amount(order.price_rub)
            if order.paid_rub:
                await callback.answer("Предоплата уже внесена.", show_alert=True)
                return
            order.paid_rub = amount
            order.status = OrderStatus.BOOKED.value
        elif kind == "remainder":
            amount = max(0, order.price_rub - order.paid_rub)
            if amount == 0:
                await callback.answer("Заказ уже полностью оплачен.", show_alert=True)
                return
            order.paid_rub = order.price_rub
            order.status = (
                OrderStatus.READY.value
                if order.requested_start_at and order.requested_start_at <= now
                else OrderStatus.BOOKED.value
            )
        else:
            amount = max(0, order.price_rub - order.paid_rub)
            if amount == 0:
                await callback.answer("Заказ уже полностью оплачен.", show_alert=True)
                return
            order.paid_rub = order.price_rub
            order.status = OrderStatus.READY.value

        session.add(
            Payment(
                order_id=order.id,
                provider="test",
                amount_rub=amount,
                status="paid",
                external_id=f"test-{order.id}-{int(now.timestamp())}",
                created_at=now,
                paid_at=now,
            )
        )
        order.updated_at = now
        await session.commit()

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.answer("Тестовая оплата прошла", show_alert=True)

    if order.status == OrderStatus.READY.value:
        await bot.send_message(
            callback.from_user.id,
            f"<b>✅ Заказ №{order.id} полностью оплачен</b>\n\n"
            "Администратор запустит рекламу из личной админ-панели.",
        )
        await bot.send_message(
            settings.owner_id,
            f"<b>💳 Заказ №{order.id} полностью оплачен</b>\n\n"
            "Откройте карточку заказа и нажмите «Активировать».",
            reply_markup=private_activation_keyboard(order.id),
        )
    else:
        await bot.send_message(
            callback.from_user.id,
            f"<b>📅 Бронь №{order.id}</b>\n\n"
            f"Оплачено: <b>{order.paid_rub}/{order.price_rub} ₽</b>.",
        )
        await bot.send_message(
            settings.owner_id,
            f"📅 По брони №{order.id} внесено {order.paid_rub}/{order.price_rub} ₽.",
        )
