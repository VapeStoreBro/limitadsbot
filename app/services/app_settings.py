from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AppSetting

STAFF_CHAT_KEY = "staff_chat_id"


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
