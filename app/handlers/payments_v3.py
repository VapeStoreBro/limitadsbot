from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery

from app.db.session import SessionFactory
from app.enums import OrderStatus
from app.models import AdOrder, Payment
from app.services.lifecycle import auto_activate_paid_order
from app.services.order_cards import register_buyer_card, update_buyer_card
from app.services.orders import deposit_amount

router = Router(name="payments_v3")


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
        await register_buyer_card(
            session,
            order,
            callback.message.chat.id,
            callback.message.message_id,
        )

        if kind == "deposit":
            amount = deposit_amount(order.price_rub)
            if order.paid_rub >= amount:
                await callback.answer("Предоплата уже внесена.", show_alert=True)
                await update_buyer_card(session, bot, order)
                return
            order.paid_rub = amount
            order.status = OrderStatus.BOOKED.value
        elif kind in {"remainder", "full"}:
            amount = max(0, order.price_rub - order.paid_rub)
            if amount == 0:
                await callback.answer("Заказ уже полностью оплачен.", show_alert=True)
                await auto_activate_paid_order(session, bot, order)
                return
            order.paid_rub = order.price_rub
            order.status = OrderStatus.READY.value
        else:
            await callback.answer("Неизвестный тип оплаты.", show_alert=True)
            return

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

        await callback.answer("✅ Тестовая оплата прошла", show_alert=True)
        if order.paid_rub >= order.price_rub:
            await auto_activate_paid_order(session, bot, order)
        else:
            await update_buyer_card(session, bot, order)
