import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from sqlalchemy import select

from app.config import get_settings
from app.db.session import SessionFactory
from app.enums import OrderStatus, PublicationKind, TariffCode
from app.keyboards import activation_keyboard
from app.models import AdOrder, Publication
from app.services.telegram_ads import refresh_user_prefix, send_ad_content


class OrderScheduler:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self.settings = get_settings()
        self._task: asyncio.Task | None = None

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
            await asyncio.sleep(20)

    async def tick(self) -> None:
        now = datetime.now(timezone.utc)
        async with SessionFactory() as session:
            active = (await session.scalars(select(AdOrder).where(
                AdOrder.status == OrderStatus.ACTIVE.value
            ))).all()
            for order in active:
                if order.ends_at and order.ends_at <= now:
                    await self._expire(session, order)
                    continue
                if order.tariff_code == TariffCode.BEST.value and order.next_publish_at and order.next_publish_at <= now:
                    await self._publish_best_copy(session, order)

            booked = (await session.scalars(select(AdOrder).where(
                AdOrder.status == OrderStatus.BOOKED.value
            ))).all()
            for order in booked:
                if order.remaining_due_at and order.remaining_due_at <= now and not order.payment_reminder_sent:
                    remaining = max(0, order.price_rub - order.paid_rub)
                    await self.bot.send_message(order.user_id, f"⏰ По брони №{order.id} пора доплатить {remaining} ₽.")
                    order.payment_reminder_sent = True
                if order.requested_start_at and order.requested_start_at <= now:
                    if order.paid_rub >= order.price_rub:
                        order.status = OrderStatus.READY.value
                        await self.bot.send_message(
                            self.settings.staff_chat_id,
                            f"🚀 Бронь №{order.id} полностью оплачена и готова к запуску.",
                            reply_markup=activation_keyboard(order.id),
                        )
                    else:
                        order.status = OrderStatus.CANCELLED.value
                        await self.bot.send_message(order.user_id, f"❌ Бронь №{order.id} отменена: остаток не оплачен вовремя.")
                    order.updated_at = now
            await session.commit()

    async def _publish_best_copy(self, session, order: AdOrder) -> None:
        now = datetime.now(timezone.utc)
        messages = await send_ad_content(self.bot, self.settings.bazaar_chat_id, order)
        for message in messages:
            session.add(Publication(
                order_id=order.id,
                chat_id=self.settings.bazaar_chat_id,
                message_id=message.message_id,
                kind=PublicationKind.COPY.value,
                created_at=now,
                deleted_at=None,
            ))
        order.next_publish_at = now + timedelta(hours=3)
        await session.flush()
        copies = (await session.scalars(select(Publication).where(
            Publication.order_id == order.id,
            Publication.kind == PublicationKind.COPY.value,
            Publication.deleted_at.is_(None),
        ).order_by(Publication.created_at.desc(), Publication.id.desc()))).all()
        grouped: list[list[Publication]] = []
        for publication in copies:
            if not grouped or abs((grouped[-1][0].created_at - publication.created_at).total_seconds()) > 2:
                grouped.append([publication])
            else:
                grouped[-1].append(publication)
        for old_group in grouped[3:]:
            for publication in old_group:
                try:
                    await self.bot.delete_message(publication.chat_id, publication.message_id)
                except Exception:
                    pass
                publication.deleted_at = now
        await session.commit()

    async def _expire(self, session, order: AdOrder) -> None:
        now = datetime.now(timezone.utc)
        if order.pinned_message_id:
            try:
                await self.bot.unpin_chat_message(self.settings.bazaar_chat_id, order.pinned_message_id)
            except Exception:
                pass
        order.status = OrderStatus.COMPLETED.value
        order.awaiting_middle_pin = False
        order.next_publish_at = None
        order.updated_at = now
        await session.commit()
        await refresh_user_prefix(session, self.bot, order.user_id)
        await self.bot.send_message(order.user_id, f"✅ Реклама по заказу №{order.id} завершена.")
