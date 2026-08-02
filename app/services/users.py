import logging
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.types import User as TelegramUser
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import SessionFactory
from app.models import Admin, User
from app.services.app_settings import get_bazaar_chat_id

logger = logging.getLogger(__name__)
OWNER_FALLBACK_ID = 6577441312

MEMBER_STATUSES = {
    ChatMemberStatus.CREATOR.value,
    ChatMemberStatus.ADMINISTRATOR.value,
    ChatMemberStatus.MEMBER.value,
}


def _status_value(raw_status: object) -> str:
    value = getattr(raw_status, "value", raw_status)
    return str(value)


async def inspect_membership(bot: Bot, user_id: int) -> tuple[str, str, str | None]:
    """Return (result, Telegram status, error) for the configured bazaar."""
    if user_id in {get_settings().owner_id, OWNER_FALLBACK_ID}:
        return "member", ChatMemberStatus.CREATOR.value, None

    async with SessionFactory() as session:
        bazaar_chat_id = await get_bazaar_chat_id(session)
    try:
        member = await bot.get_chat_member(bazaar_chat_id, user_id)
        status = _status_value(member.status)
        if status in MEMBER_STATUSES:
            return "member", status, None
        if status == ChatMemberStatus.RESTRICTED.value:
            is_member = bool(getattr(member, "is_member", False))
            return ("member" if is_member else "not_member"), status, None
        if status in {ChatMemberStatus.LEFT.value, ChatMemberStatus.KICKED.value}:
            return "not_member", status, None
        return "unknown", status, f"unexpected membership status: {status}"
    except Exception as error:
        logger.exception(
            "Unable to inspect membership for user %s in chat %s",
            user_id,
            bazaar_chat_id,
        )
        return "unknown", "unknown", f"{type(error).__name__}: {error}"


async def upsert_user(session: AsyncSession, bot: Bot, tg_user: TelegramUser) -> User:
    now = datetime.now(timezone.utc)
    membership, status, _ = await inspect_membership(bot, tg_user.id)
    is_member = membership == "member"
    user = await session.get(User, tg_user.id)
    if user is None:
        history = [tg_user.username] if tg_user.username else []
        user = User(
            id=tg_user.id,
            username=tg_user.username,
            first_name=tg_user.first_name or "",
            last_name=tg_user.last_name,
            language_code=tg_user.language_code,
            is_premium=bool(tg_user.is_premium),
            phone=None,
            is_bazaar_member=is_member,
            bazaar_status=status,
            username_history=history,
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(user)
    else:
        history = list(user.username_history or [])
        if tg_user.username and tg_user.username not in history:
            history.append(tg_user.username)
        user.username = tg_user.username
        user.first_name = tg_user.first_name or ""
        user.last_name = tg_user.last_name
        user.language_code = tg_user.language_code
        user.is_premium = bool(tg_user.is_premium)
        user.is_bazaar_member = is_member
        user.bazaar_status = status
        user.username_history = history[-20:]
        user.last_seen_at = now
    await session.commit()
    return user


async def is_admin(session: AsyncSession, user_id: int) -> bool:
    settings = get_settings()
    if user_id in {settings.owner_id, OWNER_FALLBACK_ID}:
        return True
    return await session.get(Admin, user_id) is not None
