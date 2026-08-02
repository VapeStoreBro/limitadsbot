import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyKeyboardRemove

from app.config import get_settings
from app.db.session import SessionFactory
from app.handlers.common import show_profile
from app.models import User, UserScreen
from app.services.users import upsert_user

logger = logging.getLogger(__name__)
router = Router(name="owner_emergency_entry")
OWNER_ID = 6577441312


def owner_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔐 Админ-панель", callback_data="profile:admin")],
            [InlineKeyboardButton(text="📂 Мои рекламы", callback_data="profile:orders")],
            [InlineKeyboardButton(text="📢 Разместить рекламу", callback_data="profile:buy")],
        ]
    )


async def emergency_owner_menu(message: Message, bot: Bot) -> None:
    if not message.from_user or message.from_user.id != OWNER_ID:
        return

    async with SessionFactory() as session:
        user = await session.get(User, OWNER_ID)
        if user is None:
            user = await upsert_user(session, bot, message.from_user)

    try:
        await show_profile(bot, message.chat.id, user, True, source_message=message)
        return
    except Exception:
        logger.exception("Primary owner menu failed; using emergency text menu")

    async with SessionFactory() as session:
        stale = await session.get(UserScreen, OWNER_ID)
        if stale is not None:
            await session.delete(stale)
            await session.commit()

    await bot.send_message(
        OWNER_ID,
        "<b>👤 ГЛАВНОЕ МЕНЮ ВЛАДЕЛЬЦА</b>\n\n"
        f"ID: <code>{OWNER_ID}</code>\n"
        "Права владельца подтверждены. Открываю аварийное текстовое меню.",
        reply_markup=owner_menu(),
    )


@router.message(
    CommandStart(),
    F.chat.type == ChatType.PRIVATE,
    F.from_user.id == OWNER_ID,
)
async def owner_start(message: Message, bot: Bot) -> None:
    try:
        await message.answer("Открываю меню…", reply_markup=ReplyKeyboardRemove(), disable_notification=True)
    except Exception:
        pass
    await emergency_owner_menu(message, bot)


@router.message(
    F.chat.type == ChatType.PRIVATE,
    F.from_user.id == OWNER_ID,
    F.text.in_({"🚀 Открыть бота", "Открыть бота", "🚀 Открыть", "🚀Открыть бота"}),
)
async def owner_launch(message: Message, bot: Bot) -> None:
    await emergency_owner_menu(message, bot)
