from datetime import datetime, timezone

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.types import User as TelegramUser
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Admin, User

MEMBER_STATUSES = {
    ChatMemberStatus.CREATOR,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.RESTRICTED,
}


async def inspect_membership(bot: Bot, user_id: int) -> tuple[bool, str]:
    settings = get_settings()
    try:
        member = await bot.get_chat_member(settings.bazaar_chat_id, user_id)
        return member.status in MEMBER_STATUSES, member.status.value
    except Exception:
        return False, "unknown"


async def upsert_user(session: AsyncSession, bot: Bot, tg_user: TelegramUser) -> User:
    now = datetime.now(timezone.utc)
    is_member, status = await inspect_membership(bot, tg_user.id)
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
    return await session.get(Admin, user_id) is not None
