import asyncio
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
                pass
            await asyncio.sleep(60)

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
                if offer and offer.expires_at > now:
                    continue

                if (
                    order.remaining_due_at
                    and order.remaining_due_at <= now
                    and order.requested_start_at
                    and order.requested_start_at > now
                    and order.paid_rub < order.price_rub
                    and not order.payment_reminder_sent
                ):
                    order.payment_reminder_sent = True
                    order.updated_at = now
                    await session.commit()
                    await update_buyer_card(session, self.bot, order)
                    await send_ephemeral_notice(
                        self.bot,
                        order.user_id,
                        f"<b>⏰ По брони №{order.id} пора доплатить остаток</b>\n\n"
                        "Кнопка оплаты находится в карточке заказа.",
                    )

                if order.requested_start_at and order.requested_start_at <= now:
                    if order.paid_rub >= order.price_rub:
                        order.status = OrderStatus.READY.value
                        order.updated_at = now
                        await session.commit()
                        await auto_activate_paid_order(session, self.bot, order)
                    else:
                        await complete_order(session, self.bot, order, cancelled=True)

            # This is cheap: two capacity checks and only booked rows. It also
            # catches slots freed manually outside the scheduler.
            await promote_waiting_bookings(session, self.bot)

            if (
                self._last_prefix_audit is None
                or now - self._last_prefix_audit >= timedelta(minutes=15)
            ):
                await audit_advertising_prefixes(session, self.bot)
                self._last_prefix_audit = now
