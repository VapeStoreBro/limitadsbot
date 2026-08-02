import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyKeyboardRemove

from app.db.session import SessionFactory
from app.models import User, UserScreen
from app.services.ui_screen import delete_user_input, register_user_screen
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


async def _delete_later(message: Message) -> None:
    await asyncio.sleep(1)
    try:
        await message.delete()
    except Exception:
        pass


async def emergency_owner_menu(message: Message, bot: Bot) -> None:
    """Open a guaranteed plain-text owner menu without membership or image UI."""
    if not message.from_user or message.from_user.id != OWNER_ID:
        return

    async with SessionFactory() as session:
        user = await session.get(User, OWNER_ID)
        if user is None:
            await upsert_user(session, bot, message.from_user)
        stale = await session.get(UserScreen, OWNER_ID)
        if stale is not None:
            await session.delete(stale)
            await session.commit()

    try:
        service = await bot.send_message(
            OWNER_ID,
            "Открываю меню…",
            reply_markup=ReplyKeyboardRemove(),
            disable_notification=True,
        )
        asyncio.create_task(_delete_later(service))
    except Exception:
        pass

    menu_message = await bot.send_message(
        OWNER_ID,
        "<b>👤 ГЛАВНОЕ МЕНЮ ВЛАДЕЛЬЦА</b>\n\n"
        f"ID: <code>{OWNER_ID}</code>\n"
        "Права владельца подтверждены.",
        reply_markup=owner_menu(),
    )
    async with SessionFactory() as session:
        await register_user_screen(
            session,
            OWNER_ID,
            OWNER_ID,
            menu_message.message_id,
            media_key="text:owner",
        )
    await delete_user_input(message)


@router.message(
    CommandStart(),
    F.chat.type == ChatType.PRIVATE,
    F.from_user.id == OWNER_ID,
)
async def owner_start(message: Message, bot: Bot) -> None:
    await emergency_owner_menu(message, bot)


@router.message(
    F.chat.type == ChatType.PRIVATE,
    F.from_user.id == OWNER_ID,
    F.text.in_({"🚀 Открыть бота", "Открыть бота", "🚀 Открыть", "🚀Открыть бота"}),
)
async def owner_launch(message: Message, bot: Bot) -> None:
    await emergency_owner_menu(message, bot)
