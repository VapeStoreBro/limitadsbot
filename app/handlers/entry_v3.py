import asyncio
from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
from aiogram.types import Message, ReplyKeyboardRemove

from app.db.session import SessionFactory
from app.handlers.common import show_access_result
from app.keyboards import phone_keyboard
from app.keyboards_v3 import LAUNCH_TEXT
from app.models import User
from app.services.ui_screen import delete_user_input
from app.services.users import is_admin, upsert_user

router = Router(name="entry_v3")
LAUNCH_ALIASES = {
    LAUNCH_TEXT,
    "Открыть бота",
    "🚀 Открыть",
    "🚀Открыть бота",
}


async def _delete_later(message: Message, delay: float = 1.0) -> None:
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception:
        pass


async def hide_reply_keyboard(message: Message) -> None:
    """Remove an old launcher keyboard without leaving permanent chat spam."""
    try:
        service = await message.answer(
            "Открываю…",
            reply_markup=ReplyKeyboardRemove(),
            disable_notification=True,
        )
        asyncio.create_task(_delete_later(service))
    except Exception:
        pass


async def open_bot(message: Message, bot: Bot) -> None:
    """Open the real interface directly; the launcher is never a hard gate."""
    if not message.from_user or message.chat.type != ChatType.PRIVATE:
        return

    async with SessionFactory() as session:
        user = await upsert_user(session, bot, message.from_user)
        admin = await is_admin(session, user.id)

    if not admin and not user.phone:
        await message.answer(
            "<b>📱 Подтвердите номер телефона</b>\n\n"
            "Нажмите кнопку ниже — после подтверждения главное меню откроется автоматически.",
            reply_markup=phone_keyboard(),
        )
        return

    await hide_reply_keyboard(message)
    await show_access_result(
        bot,
        message.chat.id,
        user,
        admin,
        source_message=message,
    )


@router.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
async def start_v3(message: Message, bot: Bot) -> None:
    # Telegram's own Start button now opens the interface immediately.
    await open_bot(message, bot)


@router.message(F.chat.type == ChatType.PRIVATE, F.text.in_(LAUNCH_ALIASES))
async def launch_v3(message: Message, bot: Bot) -> None:
    # Compatibility with already displayed reply-keyboard buttons.
    await open_bot(message, bot)


@router.message(F.chat.type == ChatType.PRIVATE, F.contact)
async def save_contact_v3(message: Message, bot: Bot) -> None:
    if not message.from_user or not message.contact:
        return
    if message.contact.user_id not in (None, message.from_user.id):
        await message.answer(
            "Отправьте собственный номер кнопкой ниже.",
            reply_markup=phone_keyboard(),
        )
        return

    async with SessionFactory() as session:
        user = await session.get(User, message.from_user.id)
        if user is None:
            user = await upsert_user(session, bot, message.from_user)
        user.phone = message.contact.phone_number
        user.last_seen_at = datetime.now(timezone.utc)
        await session.commit()
        admin = await is_admin(session, user.id)

    await hide_reply_keyboard(message)
    await show_access_result(
        bot,
        message.chat.id,
        user,
        admin,
        source_message=message,
    )
