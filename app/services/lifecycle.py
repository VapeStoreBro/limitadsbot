from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.enums import OrderStatus, TariffCode
from app.models import AdOrder, OrderNotice, UserBlock
from app.rules import advertising_prefix
from app.services.order_cards import update_buyer_card, update_staff_card
from app.services.orders import find_next_available_slot, slot_available
from app.services.telegram_ads import activate_order, finish_order, refresh_user_prefix

settings = get_settings()


async def notice_sent(session: AsyncSession, order_id: int, code: str) -> bool:
    return (
        await session.scalar(
            select(OrderNotice).where(
                OrderNotice.order_id == order_id,
                OrderNotice.code == code,
            )
        )
    ) is not None


async def mark_notice(session: AsyncSession, order_id: int, code: str) -> None:
    if await notice_sent(session, order_id, code):
        return
    session.add(
        OrderNotice(
            order_id=order_id,
            code=code,
            sent_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()


async def auto_activate_paid_order(
    session: AsyncSession,
    bot: Bot,
    order: AdOrder,
) -> bool:
    """Start after moderation + full payment, while respecting tariff capacity."""
    now = datetime.now(timezone.utc)
    if await session.get(UserBlock, order.user_id):
        order.status = OrderStatus.CANCELLED.value
        order.updated_at = now
        await session.commit()
        await update_buyer_card(session, bot, order)
        return False
    if order.paid_rub < order.price_rub:
        await update_buyer_card(session, bot, order)
        return False
    if order.status == OrderStatus.ACTIVE.value:
        await update_buyer_card(session, bot, order)
        return True
    if order.requested_start_at and order.requested_start_at > now:
        order.status = OrderStatus.BOOKED.value
        order.updated_at = now
        await session.commit()
        await update_buyer_card(session, bot, order)
        return False

    available = await slot_available(
        session,
        order.tariff_code,
        now,
        now + timedelta(hours=order.duration_hours),
        order.id,
    )
    if not available and order.tariff_code != TariffCode.STANDARD.value:
        next_slot = await find_next_available_slot(
            session,
            order.tariff_code,
            order.duration_hours,
            now,
        )
        order.status = OrderStatus.BOOKED.value
        order.requested_start_at = next_slot
        order.requested_end_at = next_slot + timedelta(hours=order.duration_hours)
        order.updated_at = now
        await session.commit()
        await update_buyer_card(session, bot, order)
        return False

    order.status = OrderStatus.READY.value
    order.updated_at = now
    await session.commit()
    try:
        activated = await activate_order(
            session,
            bot,
            order,
            actor_id=order.moderated_by or settings.owner_id,
        )
    except Exception:
        order.status = OrderStatus.READY.value
        order.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await update_buyer_card(session, bot, order)
        return False

    await update_buyer_card(session, bot, order)
    if activated:
        await update_staff_card(
            session,
            bot,
            order,
            f"<b>✅ Заявка №{order.id} оплачена и реклама запущена автоматически</b>",
        )
    return activated


async def send_three_day_warning(
    session: AsyncSession,
    bot: Bot,
    order: AdOrder,
) -> None:
    if order.duration_hours <= 72 or not order.ends_at:
        return
    remaining = order.ends_at - datetime.now(timezone.utc)
    if remaining <= timedelta(0) or remaining > timedelta(days=3):
        return
    code = "ends_in_3_days"
    if await notice_sent(session, order.id, code):
        return
    await bot.send_message(
        order.user_id,
        f"<b>⏳ Реклама №{order.id} закончится через 3 дня</b>\n\n"
        "Продлить размещение можно через администрацию. Управление текущим постом остаётся в карточке заказа.",
    )
    await mark_notice(session, order.id, code)


async def complete_order(
    session: AsyncSession,
    bot: Bot,
    order: AdOrder,
    *,
    cancelled: bool = False,
) -> None:
    target = OrderStatus.CANCELLED.value if cancelled else OrderStatus.COMPLETED.value
    await finish_order(session, bot, order, status=target)
    await update_buyer_card(session, bot, order)
    code = f"finished:{target}"
    if not await notice_sent(session, order.id, code):
        title = "отменена" if cancelled else "завершена"
        await bot.send_message(
            order.user_id,
            f"<b>🏁 Реклама №{order.id} {title}</b>\n\n"
            "Закреп снят, автоматические публикации остановлены, рекламный префикс обновлён.",
        )
        await mark_notice(session, order.id, code)


async def audit_advertising_prefixes(session: AsyncSession, bot: Bot) -> None:
    """Low-frequency audit for users who ever had an order."""
    user_ids = (
        await session.scalars(select(AdOrder.user_id).distinct().order_by(AdOrder.user_id))
    ).all()
    for user_id in user_ids:
        active = (
            await session.scalars(
                select(AdOrder).where(
                    AdOrder.user_id == user_id,
                    AdOrder.status == OrderStatus.ACTIVE.value,
                    AdOrder.ends_at.is_not(None),
                )
            )
        ).all()
        try:
            member = await bot.get_chat_member(settings.bazaar_chat_id, user_id)
        except Exception:
            continue
        custom_title = getattr(member, "custom_title", None) or ""
        if active:
            furthest = max(order.ends_at for order in active if order.ends_at)
            expected = advertising_prefix(furthest, settings.timezone)
            if custom_title != expected:
                await refresh_user_prefix(session, bot, user_id)
        elif custom_title.startswith("Реклама до"):
            await refresh_user_prefix(session, bot, user_id)
