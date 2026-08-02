from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram import Bot
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import OrderStatus, TariffCode
from app.models import AdOrder, BookingOffer, UserBlock
from app.services.order_cards import update_buyer_card
from app.services.telegram_ads import activate_order, finish_order
from app.services.ui_screen import send_ephemeral_notice

CAPACITY = {
    TariffCode.MIDDLE.value: 3,
    TariffCode.BEST.value: 1,
}
OFFER_LIFETIME = timedelta(hours=24)


async def _reserved_count(
    session: AsyncSession,
    tariff: str,
    now: datetime,
) -> int:
    active = await session.scalar(
        select(func.count()).select_from(AdOrder).where(
            AdOrder.tariff_code == tariff,
            AdOrder.status == OrderStatus.ACTIVE.value,
        )
    )
    offers = await session.scalar(
        select(func.count())
        .select_from(BookingOffer)
        .join(AdOrder, AdOrder.id == BookingOffer.order_id)
        .where(
            AdOrder.tariff_code == tariff,
            AdOrder.status == OrderStatus.BOOKED.value,
            BookingOffer.expires_at > now,
        )
    )
    return int(active or 0) + int(offers or 0)


async def promote_waiting_bookings(
    session: AsyncSession,
    bot: Bot,
    tariff_code: str | None = None,
) -> None:
    """Offer newly free constrained slots in reservation order."""
    now = datetime.now(timezone.utc)
    tariffs = [tariff_code] if tariff_code in CAPACITY else list(CAPACITY)

    for tariff in tariffs:
        available = CAPACITY[tariff] - await _reserved_count(session, tariff, now)
        if available <= 0:
            continue

        candidates = (
            await session.scalars(
                select(AdOrder)
                .where(
                    AdOrder.tariff_code == tariff,
                    AdOrder.status == OrderStatus.BOOKED.value,
                    AdOrder.requested_start_at.is_not(None),
                    AdOrder.requested_start_at > now,
                )
                .order_by(AdOrder.requested_start_at, AdOrder.created_at, AdOrder.id)
            )
        ).all()

        for order in candidates:
            if available <= 0:
                break
            if await session.get(BookingOffer, order.id):
                continue
            if await session.get(UserBlock, order.user_id):
                continue

            order.requested_start_at = now
            order.requested_end_at = now + timedelta(hours=order.duration_hours)
            order.updated_at = now

            if order.paid_rub >= order.price_rub:
                order.status = OrderStatus.READY.value
                await session.commit()
                activated = await activate_order(
                    session,
                    bot,
                    order,
                    actor_id=order.moderated_by or 0,
                )
                await update_buyer_card(session, bot, order)
                if activated:
                    await send_ephemeral_notice(
                        bot,
                        order.user_id,
                        f"<b>🚀 Место освободилось</b>\n\n"
                        f"Полностью оплаченная реклама №{order.id} запущена автоматически.",
                    )
                    available -= 1
                continue

            expires = now + OFFER_LIFETIME
            order.remaining_due_at = expires
            order.payment_reminder_sent = True
            session.add(
                BookingOffer(
                    order_id=order.id,
                    offered_at=now,
                    expires_at=expires,
                )
            )
            await session.commit()
            await update_buyer_card(session, bot, order)
            await send_ephemeral_notice(
                bot,
                order.user_id,
                f"<b>🔥 Освободилось место для рекламы №{order.id}</b>\n\n"
                "Бот удерживает его 24 часа. Доплатите остаток кнопкой в карточке заказа, "
                "и реклама запустится сразу.",
            )
            available -= 1


async def expire_booking_offers(session: AsyncSession, bot: Bot) -> None:
    """Release unpaid early-start offers and immediately pass the slot onward."""
    now = datetime.now(timezone.utc)
    offers = (
        await session.scalars(
            select(BookingOffer)
            .where(BookingOffer.expires_at <= now)
            .order_by(BookingOffer.expires_at)
        )
    ).all()
    affected_tariffs: set[str] = set()

    for offer in offers:
        order = await session.get(AdOrder, offer.order_id)
        await session.delete(offer)
        if not order:
            await session.commit()
            continue
        affected_tariffs.add(order.tariff_code)

        if order.paid_rub >= order.price_rub:
            order.status = OrderStatus.READY.value
            order.updated_at = now
            await session.commit()
            await activate_order(session, bot, order, actor_id=order.moderated_by or 0)
            await update_buyer_card(session, bot, order)
            continue

        await finish_order(session, bot, order, status=OrderStatus.CANCELLED.value)
        await update_buyer_card(session, bot, order)
        await send_ephemeral_notice(
            bot,
            order.user_id,
            f"<b>⌛ Срок доплаты по рекламе №{order.id} истёк</b>\n\n"
            "За 24 часа остаток не был внесён, поэтому место передано следующему в очереди.",
        )

    for tariff in affected_tariffs:
        await promote_waiting_bookings(session, bot, tariff)
