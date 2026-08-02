from datetime import datetime, timezone

from sqlalchemy import select

from app.config import get_settings
from app.db.session import SessionFactory, engine
from app.enums import DurationCode, TariffCode
from app.models import Admin, Base, TariffPrice, User

DEFAULT_PRICES = {
    (TariffCode.STANDARD, DurationCode.DAY): (500, 24),
    (TariffCode.STANDARD, DurationCode.WEEK): (1000, 24 * 7),
    (TariffCode.STANDARD, DurationCode.MONTH): (1500, 24 * 30),
    (TariffCode.MIDDLE, DurationCode.DAY): (700, 24),
    (TariffCode.MIDDLE, DurationCode.WEEK): (1400, 24 * 7),
    (TariffCode.MIDDLE, DurationCode.MONTH): (2000, 24 * 30),
    (TariffCode.BEST, DurationCode.DAY): (1500, 24),
    (TariffCode.BEST, DurationCode.WEEK): (2000, 24 * 7),
    (TariffCode.BEST, DurationCode.MONTH): (2700, 24 * 30),
}


async def bootstrap_database() -> None:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with SessionFactory() as session:
        owner = await session.get(User, settings.owner_id)
        if owner is None:
            owner = User(
                id=settings.owner_id,
                username="vapestorebro",
                first_name="Owner",
                last_name=None,
                language_code="ru",
                is_premium=False,
                phone=None,
                is_bazaar_member=True,
                bazaar_status="creator",
                username_history=["vapestorebro"],
                first_seen_at=now,
                last_seen_at=now,
            )
            session.add(owner)
            await session.flush()
        if await session.get(Admin, settings.owner_id) is None:
            session.add(Admin(user_id=settings.owner_id, role="owner", added_by=settings.owner_id, added_at=now))
        for (tariff, duration), (price, hours) in DEFAULT_PRICES.items():
            row = await session.scalar(select(TariffPrice).where(
                TariffPrice.tariff_code == tariff.value,
                TariffPrice.duration_code == duration.value,
            ))
            if row is None:
                session.add(TariffPrice(
                    tariff_code=tariff.value,
                    duration_code=duration.value,
                    price_rub=price,
                    duration_hours=hours,
                ))
        await session.commit()
