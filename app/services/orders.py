from datetime import datetime, timedelta, timezone
from math import ceil

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import OrderStatus, TariffCode
from app.models import AdOrder, TariffPrice, UserPrice


async def get_price(
    session: AsyncSession,
    user_id: int,
    tariff_code: str,
    duration_code: str,
) -> tuple[int, int, int | None]:
    custom = await session.scalar(
        select(UserPrice).where(
            UserPrice.user_id == user_id,
            UserPrice.tariff_code == tariff_code,
            UserPrice.duration_code == duration_code,
        )
    )
    base = await session.scalar(
        select(TariffPrice).where(
            TariffPrice.tariff_code == tariff_code,
            TariffPrice.duration_code == duration_code,
        )
    )
    if base is None:
        raise RuntimeError("Tariff price is not configured")
    if custom:
        return custom.price_rub, base.duration_hours, custom.announced_discount_percent
    return base.price_rub, base.duration_hours, None


async def prices_for_user(
    session: AsyncSession,
    user_id: int,
    tariff_code: str,
) -> dict[str, int]:
    base_rows = (
        await session.scalars(
            select(TariffPrice).where(TariffPrice.tariff_code == tariff_code)
        )
    ).all()
    result = {row.duration_code: row.price_rub for row in base_rows}
    custom_rows = (
        await session.scalars(
            select(UserPrice).where(
                UserPrice.user_id == user_id,
                UserPrice.tariff_code == tariff_code,
            )
        )
    ).all()
    for row in custom_rows:
        result[row.duration_code] = row.price_rub
    return result


async def slot_available(
    session: AsyncSession,
    tariff_code: str,
    start_at: datetime,
    end_at: datetime,
    ignore_order_id: int | None = None,
) -> bool:
    if tariff_code == TariffCode.STANDARD.value:
        return True
    limit = 3 if tariff_code == TariffCode.MIDDLE.value else 1
    conditions = [
        AdOrder.tariff_code == tariff_code,
        AdOrder.status.in_(
            [
                OrderStatus.BOOKED.value,
                OrderStatus.READY.value,
                OrderStatus.ACTIVE.value,
            ]
        ),
        func.coalesce(AdOrder.requested_start_at, AdOrder.activated_at) < end_at,
        func.coalesce(AdOrder.requested_end_at, AdOrder.ends_at) > start_at,
    ]
    if ignore_order_id is not None:
        conditions.append(AdOrder.id != ignore_order_id)
    count = await session.scalar(
        select(func.count()).select_from(AdOrder).where(and_(*conditions))
    )
    return int(count or 0) < limit


async def find_next_available_slot(
    session: AsyncSession,
    tariff_code: str,
    duration_hours: int,
    from_at: datetime | None = None,
) -> datetime:
    candidate = (from_at or datetime.now(timezone.utc)).replace(second=0, microsecond=0)
    if candidate.minute:
        candidate = candidate.replace(minute=0) + timedelta(hours=1)
    for _ in range(24 * 180):
        if await slot_available(
            session,
            tariff_code,
            candidate,
            candidate + timedelta(hours=duration_hours),
        ):
            return candidate
        candidate += timedelta(hours=1)
    raise RuntimeError("No advertising slot is available within 180 days")


async def create_order(
    session: AsyncSession,
    *,
    user_id: int,
    tariff_code: str,
    duration_code: str,
    content_text: str,
    media: list[dict],
    buttons: list[dict],
    requested_start_at: datetime | None,
) -> AdOrder:
    price, hours, _ = await get_price(session, user_id, tariff_code, duration_code)
    now = datetime.now(timezone.utc)
    requested_end_at = (
        requested_start_at + timedelta(hours=hours) if requested_start_at else None
    )
    order = AdOrder(
        user_id=user_id,
        tariff_code=tariff_code,
        duration_code=duration_code,
        duration_hours=hours,
        price_rub=price,
        status=OrderStatus.MODERATION.value,
        content_text=content_text,
        media=media,
        buttons=buttons,
        requested_start_at=requested_start_at,
        requested_end_at=requested_end_at,
        remaining_due_at=(
            requested_start_at - timedelta(hours=24) if requested_start_at else None
        ),
        payment_reminder_sent=False,
        paid_rub=0,
        awaiting_middle_pin=False,
        pin_changes_used=0,
        created_at=now,
        updated_at=now,
    )
    session.add(order)
    await session.commit()
    await session.refresh(order)
    return order


def deposit_amount(price_rub: int) -> int:
    return ceil(price_rub / 2)
