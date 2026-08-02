from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AppSetting

STAFF_CHAT_KEY = "staff_chat_id"
BAZAAR_CHAT_KEY = "bazaar_chat_id"
BAZAAR_URL_KEY = "bazaar_url"
STATS_RESET_AT_KEY = "stats_reset_at"


async def get_setting(session: AsyncSession, key: str, default: str | None = None) -> str | None:
    row = await session.get(AppSetting, key)
    return row.value if row else default


async def set_setting(
    session: AsyncSession,
    key: str,
    value: str,
    actor_id: int | None,
) -> None:
    row = await session.get(AppSetting, key)
    now = datetime.now(timezone.utc)
    if row is None:
        session.add(
            AppSetting(
                key=key,
                value=value,
                updated_by=actor_id,
                updated_at=now,
            )
        )
    else:
        row.value = value
        row.updated_by = actor_id
        row.updated_at = now
    await session.commit()


async def get_staff_chat_id(session: AsyncSession) -> int:
    settings = get_settings()
    raw = await get_setting(session, STAFF_CHAT_KEY, str(settings.staff_chat_id))
    return int(raw or settings.staff_chat_id)


async def get_bazaar_chat_id(session: AsyncSession) -> int:
    settings = get_settings()
    raw = await get_setting(session, BAZAAR_CHAT_KEY, str(settings.bazaar_chat_id))
    return int(raw or settings.bazaar_chat_id)


async def get_bazaar_url(session: AsyncSession) -> str:
    settings = get_settings()
    return str(await get_setting(session, BAZAAR_URL_KEY, settings.bazaar_url) or settings.bazaar_url)


async def get_stats_reset_at(session: AsyncSession) -> datetime | None:
    raw = await get_setting(session, STATS_RESET_AT_KEY)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
