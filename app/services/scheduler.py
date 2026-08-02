import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from sqlalchemy import select

from app.db.session import SessionFactory
from app.enums import OrderStatus, TariffCode
from app.models import AdOrder, BookingOffer, UserBlock
from app.services.booking_queue import expire_booking_offers, promote_waiting_bookings
from app.services.lifecycle import (
    audit_advertising_prefixes,
    auto_activate_paid_order,
    complete_order,
    send_three_day_warning,
)
from app.services.order_cards import update_buyer_card
from app.services.telegram_ads import publish_best_copy
from app.services.ui_screen import send_ephemeral_notice

logger = logging.getLogger(__name__)
FINAL_PAYMENT_WINDOW = timedelta(hours=24)


class OrderScheduler:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._task: asyncio.Task | None = None
        self._last_prefix_audit: datetime | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="order-scheduler")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("Order scheduler tick failed")
            await asyncio.sleep(30)

    async def tick(self) -> None:
        now = datetime.now(timezone.utc)
        async with SessionFactory() as session:
            await expire_booking_offers(session, self.bot)

            ready = (
                await session.scalars(
                    select(AdOrder).where(
                        AdOrder.status == OrderStatus.READY.value,
                        AdOrder.paid_rub >= AdOrder.price_rub,
                    )
                )
            ).all()
            for order in ready:
                await auto_activate_paid_order(session, self.bot, order)

            active = (
                await session.scalars(
                    select(AdOrder).where(AdOrder.status == OrderStatus.ACTIVE.value)
                )
            ).all()
            for order in active:
                if await session.get(UserBlock, order.user_id):
                    await complete_order(session, self.bot, order, cancelled=True)
                    continue
                await send_three_day_warning(session, self.bot, order)
                if order.ends_at and order.ends_at <= now:
                    await complete_order(session, self.bot, order)
                    continue
                if (
                    order.tariff_code == TariffCode.BEST.value
                    and order.next_publish_at
                    and order.next_publish_at <= now
                ):
                    await publish_best_copy(session, self.bot, order)
                    await update_buyer_card(session, self.bot, order)

            booked = (
                await session.scalars(
                    select(AdOrder)
                    .where(AdOrder.status == OrderStatus.BOOKED.value)
                    .order_by(AdOrder.requested_start_at, AdOrder.created_at)
                )
            ).all()
            for order in booked:
                if await session.get(UserBlock, order.user_id):
                    await complete_order(session, self.bot, order, cancelled=True)
                    continue

                offer = await session.get(BookingOffer, order.id)

                # Remove deadlines created by the old rule. A deposit alone must
                # never start the 24-hour countdown.
                if (
                    not offer
                    and order.paid_rub < order.price_rub
                    and order.requested_start_at
                    and order.requested_start_at > now
                    and order.remaining_due_at is not None
                ):
                    order.remaining_due_at = None
                    order.payment_reminder_sent = False
                    order.updated_at = now
                    await session.commit()
                    await update_buyer_card(session, self.bot, order)

                if order.requested_start_at and order.requested_start_at <= now:
                    if order.paid_rub >= order.price_rub:
                        order.status = OrderStatus.READY.value
                        order.updated_at = now
                        await session.commit()
                        await auto_activate_paid_order(session, self.bot, order)
                        continue

                    if not offer:
                        expires = now + FINAL_PAYMENT_WINDOW
                        offer = BookingOffer(
                            order_id=order.id,
                            offered_at=now,
                            expires_at=expires,
                        )
                        session.add(offer)
                        order.remaining_due_at = expires
                        order.payment_reminder_sent = False
                        order.updated_at = now
                        await session.commit()
                        await update_buyer_card(session, self.bot, order)
                        await send_ephemeral_notice(
                            self.bot,
                            order.user_id,
                            f"<b>💳 Место по брони №{order.id} готово</b>\n\n"
                            "Теперь начался срок на внесение остатка: <b>24 часа</b>. "
                            "Кнопка оплаты находится в карточке заказа.",
                            seconds=30,
                        )

                deadline = offer.expires_at if offer else order.remaining_due_at
                if order.paid_rub < order.price_rub and deadline:
                    remaining = deadline - now
                    if remaining <= timedelta(hours=3) and not order.payment_reminder_sent:
                        order.payment_reminder_sent = True
                        order.updated_at = now
                        await session.commit()
                        await update_buyer_card(session, self.bot, order)
                        await send_ephemeral_notice(
                            self.bot,
                            order.user_id,
                            f"<b>⏰ По брони №{order.id} осталось меньше трёх часов</b>\n\n"
                            "Доплатите остаток кнопкой в карточке заказа.",
                            seconds=30,
                        )

            await promote_waiting_bookings(session, self.bot)

            if (
                self._last_prefix_audit is None
                or now - self._last_prefix_audit >= timedelta(minutes=15)
            ):
                await audit_advertising_prefixes(session, self.bot)
                self._last_prefix_audit = now
