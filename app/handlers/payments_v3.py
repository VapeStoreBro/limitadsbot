import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import select

from app.db.session import SessionFactory
from app.enums import OrderStatus
from app.models import AdOrder, BookingOffer, Payment
from app.services.lifecycle import auto_activate_paid_order
from app.services.order_cards import update_buyer_card
from app.services.orders import deposit_amount
from app.services.ui_screen import send_ephemeral_notice

router = Router(name="payments_v3")
_PAYMENT_LOCKS: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


@router.callback_query(F.data.startswith("testpay:"))
async def test_payment_v3(callback: CallbackQuery, bot: Bot) -> None:
    _, raw_id, kind = callback.data.split(":", 2)
    order_id = int(raw_id)
    lock = _PAYMENT_LOCKS[order_id]

    if lock.locked():
        await callback.answer(
            "Оплата этого заказа уже обрабатывается. Не нажимайте повторно.",
            show_alert=True,
        )
        return

    await callback.answer("Обрабатываю оплату…")
    async with lock:
        now = datetime.now(timezone.utc)
        async with SessionFactory() as session:
            order = await session.scalar(
                select(AdOrder).where(AdOrder.id == order_id).with_for_update()
            )
            if not order or order.user_id != callback.from_user.id:
                await send_ephemeral_notice(bot, callback.from_user.id, "Заказ не найден.")
                return

            amount = 0
            changed = False
            if kind == "deposit":
                required = deposit_amount(order.price_rub)
                if order.paid_rub < required:
                    amount = required - order.paid_rub
                    order.paid_rub = required
                    order.status = OrderStatus.BOOKED.value
                    deadline = now + timedelta(hours=24)
                    if order.requested_start_at and order.requested_start_at < deadline:
                        deadline = order.requested_start_at
                    order.remaining_due_at = deadline
                    order.payment_reminder_sent = False
                    changed = True
            elif kind in {"remainder", "full"}:
                amount = max(0, order.price_rub - order.paid_rub)
                if amount > 0:
                    order.paid_rub = order.price_rub
                    order.status = OrderStatus.READY.value
                    order.remaining_due_at = None
                    order.payment_reminder_sent = True
                    offer = await session.get(BookingOffer, order.id)
                    if offer:
                        await session.delete(offer)
                    changed = True
            else:
                await send_ephemeral_notice(
                    bot,
                    callback.from_user.id,
                    "Неизвестный тип оплаты.",
                )
                return

            if changed:
                session.add(
                    Payment(
                        order_id=order.id,
                        provider="test",
                        amount_rub=amount,
                        status="paid",
                        external_id=f"test-{order.id}-{int(now.timestamp() * 1_000_000)}",
                        created_at=now,
                        paid_at=now,
                    )
                )
                order.updated_at = now
                await session.commit()

            await update_buyer_card(
                session,
                bot,
                order,
                source_message=callback.message,
            )

            if order.paid_rub >= order.price_rub:
                await auto_activate_paid_order(session, bot, order)

            if changed:
                extra = (
                    "\nНа внесение остатка даётся <b>24 часа</b>. За три часа до окончания бот напомнит."
                    if kind == "deposit"
                    else "\nКарточка заказа обновлена."
                )
                await send_ephemeral_notice(
                    bot,
                    order.user_id,
                    f"<b>✅ Оплата {amount} ₽ принята</b>{extra}",
                    seconds=30,
                )
            else:
                await send_ephemeral_notice(
                    bot,
                    order.user_id,
                    "<b>✅ Этот этап оплаты уже выполнен</b>\n\n"
                    "Повторное списание не создавалось.",
                )
