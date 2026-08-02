from datetime import datetime, timezone

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import OrderStatus
from app.models import AdOrder, UserBlock
from app.services.telegram_ads import finish_order

TERMINAL_STATUSES = {
    OrderStatus.COMPLETED.value,
    OrderStatus.CANCELLED.value,
    OrderStatus.REJECTED.value,
}


async def get_user_block(session: AsyncSession, user_id: int) -> UserBlock | None:
    return await session.get(UserBlock, user_id)


async def is_user_blocked(session: AsyncSession, user_id: int) -> bool:
    return await get_user_block(session, user_id) is not None


async def block_user(
    session: AsyncSession,
    bot: Bot,
    user_id: int,
    actor_id: int,
    reason: str = "Заблокирован администрацией",
) -> UserBlock:
    now = datetime.now(timezone.utc)
    block = await session.get(UserBlock, user_id)
    if block is None:
        block = UserBlock(
            user_id=user_id,
            reason=reason,
            blocked_by=actor_id,
            blocked_at=now,
        )
        session.add(block)
    else:
        block.reason = reason
        block.blocked_by = actor_id
        block.blocked_at = now
    await session.commit()

    active_orders = (
        await session.scalars(
            select(AdOrder).where(
                AdOrder.user_id == user_id,
                AdOrder.status == OrderStatus.ACTIVE.value,
            )
        )
    ).all()
    for order in active_orders:
        await finish_order(
            session,
            bot,
            order,
            status=OrderStatus.CANCELLED.value,
        )

    pending_orders = (
        await session.scalars(
            select(AdOrder).where(
                AdOrder.user_id == user_id,
                AdOrder.status.not_in(TERMINAL_STATUSES | {OrderStatus.ACTIVE.value}),
            )
        )
    ).all()
    for order in pending_orders:
        order.status = OrderStatus.CANCELLED.value
        order.awaiting_middle_pin = False
        order.next_publish_at = None
        order.updated_at = now
    await session.commit()
    return block


async def unblock_user(session: AsyncSession, user_id: int) -> bool:
    block = await session.get(UserBlock, user_id)
    if block is None:
        return False
    await session.delete(block)
    await session.commit()
    return True
