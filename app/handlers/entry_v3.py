from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.db.session import SessionFactory
from app.handlers.common import show_access_result
from app.keyboards import phone_keyboard
from app.keyboards_v3 import LAUNCH_TEXT, launcher_keyboard
from app.models import User
from app.services.ui_screen import delete_user_input
from app.services.users import is_admin, upsert_user

router = Router(name="entry_v3")


async def offer_launcher(message: Message, bot: Bot) -> None:
    if not message.from_user or message.chat.type != ChatType.PRIVATE:
        return
    async with SessionFactory() as session:
        user = await upsert_user(session, bot, message.from_user)
        admin = await is_admin(session, user.id)

    if not admin and not user.phone:
        await message.answer(
            "<b>📱 Подтвердите номер телефона</b>\n\n"
            "Нажмите кнопку ниже — после этого станет доступно главное меню.",
            reply_markup=phone_keyboard(),
        )
        return

    await message.answer(
        "<b>Limit Ads готов</b>\n\nНажмите кнопку один раз, чтобы открыть главное меню.",
        reply_markup=launcher_keyboard(),
    )


@router.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
async def start_v3(message: Message, bot: Bot) -> None:
    await offer_launcher(message, bot)


@router.message(F.chat.type == ChatType.PRIVATE, F.text == LAUNCH_TEXT)
async def launch_v3(message: Message, bot: Bot) -> None:
    if not message.from_user:
        return
    async with SessionFactory() as session:
        user = await upsert_user(session, bot, message.from_user)
        admin = await is_admin(session, user.id)

    if not admin and not user.phone:
        await message.answer(
            "<b>📱 Сначала отправьте номер телефона</b>",
            reply_markup=phone_keyboard(),
        )
        return

    await delete_user_input(message)
    await show_access_result(bot, message.chat.id, user, admin)


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

    await delete_user_input(message)
    await show_access_result(bot, message.chat.id, user, admin)
