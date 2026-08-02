import asyncio
from collections import defaultdict

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import select

from app.db.session import SessionFactory
from app.models import AdOrder
from app.payments.service import apply_succeeded_transaction, create_transaction
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
        async with SessionFactory() as session:
            order = await session.scalar(
                select(AdOrder).where(AdOrder.id == order_id).with_for_update()
            )
            if not order or order.user_id != callback.from_user.id:
                await send_ephemeral_notice(bot, callback.from_user.id, "Заказ не найден.")
                return

            if kind == "deposit":
                amount = max(0, deposit_amount(order.price_rub) - order.paid_rub)
            elif kind in {"remainder", "full"}:
                amount = max(0, order.price_rub - order.paid_rub)
            else:
                await send_ephemeral_notice(
                    bot,
                    callback.from_user.id,
                    "Неизвестный тип оплаты.",
                )
                return

            if amount == 0:
                await send_ephemeral_notice(
                    bot,
                    order.user_id,
                    "<b>✅ Этот этап оплаты уже выполнен</b>\n\n"
                    "Повторная транзакция не создавалась.",
                )
                return

            transaction = await create_transaction(
                session,
                order,
                provider="test",
                kind=kind,
                amount_rub=amount,
            )
            await session.commit()
            applied = await apply_succeeded_transaction(
                session,
                bot,
                transaction.transaction_id,
                provider_payment_id=f"test-{transaction.transaction_id}",
                payload={
                    "source": "test_button",
                    "transaction_id": transaction.transaction_id,
                },
            )

            if not applied:
                await send_ephemeral_notice(
                    bot,
                    order.user_id,
                    "<b>✅ Эта транзакция уже обработана</b>\n\n"
                    "Повторного начисления не было.",
                )
                return

            if kind == "deposit":
                text = (
                    f"<b>✅ Предоплата {amount} ₽ принята</b>\n\n"
                    "Таймер на внесение остатка пока не запущен. "
                    "Он начнётся только тогда, когда место будет готово "
                    "к окончательной покупке или освободится раньше."
                )
            else:
                text = (
                    f"<b>✅ Оплата {amount} ₽ принята</b>\n\n"
                    "Карточка заказа обновлена."
                )
            await send_ephemeral_notice(bot, order.user_id, text, seconds=30)
