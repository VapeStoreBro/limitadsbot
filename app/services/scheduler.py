import asyncio
from contextlib import suppress
from datetime import datetime, timezone

from aiogram import Bot
from sqlalchemy import select

from app.config import get_settings
from app.db.session import SessionFactory
from app.enums import OrderStatus, TariffCode
from app.keyboards_v3 import home_keyboard, private_activation_keyboard
from app.models import AdOrder, UserBlock
from app.services.telegram_ads import finish_order, publish_best_copy


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
            active = (
                await session.scalars(
                    select(AdOrder).where(
                        AdOrder.status == OrderStatus.ACTIVE.value
                    )
                )
            ).all()
            for order in active:
                if await session.get(UserBlock, order.user_id):
                    await finish_order(
                        session,
                        self.bot,
                        order,
                        status=OrderStatus.CANCELLED.value,
                    )
                    continue
                if order.ends_at and order.ends_at <= now:
                    await finish_order(session, self.bot, order)
                    await self.bot.send_message(
                        order.user_id,
                        f"<b>✅ Реклама по заказу №{order.id} завершена</b>",
                        reply_markup=home_keyboard(),
                    )
                    continue
                if (
                    order.tariff_code == TariffCode.BEST.value
                    and order.next_publish_at
                    and order.next_publish_at <= now
                ):
                    await publish_best_copy(session, self.bot, order)

            booked = (
                await session.scalars(
                    select(AdOrder).where(
                        AdOrder.status == OrderStatus.BOOKED.value
                    )
                )
            ).all()
            for order in booked:
                if await session.get(UserBlock, order.user_id):
                    order.status = OrderStatus.CANCELLED.value
                    order.updated_at = now
                    continue
                if (
                    order.remaining_due_at
                    and order.remaining_due_at <= now
                    and not order.payment_reminder_sent
                ):
                    remaining = max(0, order.price_rub - order.paid_rub)
                    await self.bot.send_message(
                        order.user_id,
                        f"<b>⏰ По брони №{order.id} пора доплатить {remaining} ₽</b>",
                        reply_markup=home_keyboard(),
                    )
                    order.payment_reminder_sent = True
                if order.requested_start_at and order.requested_start_at <= now:
                    if order.paid_rub >= order.price_rub:
                        order.status = OrderStatus.READY.value
                        await self.bot.send_message(
                            self.settings.owner_id,
                            f"🚀 Бронь №{order.id} полностью оплачена и готова к запуску.",
                            reply_markup=private_activation_keyboard(order.id),
                        )
                    else:
                        order.status = OrderStatus.CANCELLED.value
                        await self.bot.send_message(
                            order.user_id,
                            f"<b>❌ Бронь №{order.id} отменена</b>\n\n"
                            "Остаток не был оплачен вовремя.",
                            reply_markup=home_keyboard(),
                        )
                    order.updated_at = now
            await session.commit()
