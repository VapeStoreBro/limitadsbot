from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InputMediaPhoto, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionFactory
from app.models import UserScreen
from app.services.price_card import ensure_main_menu_card


async def register_user_screen(
    session: AsyncSession,
    user_id: int,
    chat_id: int,
    message_id: int,
    *,
    media_key: str = "unknown",
) -> UserScreen:
    now = datetime.now(timezone.utc)
    screen = await session.get(UserScreen, user_id)
    if screen is None:
        screen = UserScreen(
            user_id=user_id,
            chat_id=chat_id,
            message_id=message_id,
            media_key=media_key,
            created_at=now,
            updated_at=now,
        )
        session.add(screen)
    else:
        screen.chat_id = chat_id
        screen.message_id = message_id
        screen.media_key = media_key
        screen.updated_at = now
    await session.commit()
    return screen


def _desired_media(
    *,
    image_path: str | Path | None,
    photo_file_id: str | None,
    caption: str,
) -> InputMediaPhoto:
    if photo_file_id:
        media = photo_file_id
    else:
        path = Path(image_path or ensure_main_menu_card())
        media = FSInputFile(path)
    return InputMediaPhoto(media=media, caption=caption, parse_mode="HTML")


async def _edit_same_media(
    bot: Bot,
    screen: UserScreen,
    text: str,
    markup: InlineKeyboardMarkup | None,
) -> bool:
    try:
        await bot.edit_message_caption(
            chat_id=screen.chat_id,
            message_id=screen.message_id,
            caption=text,
            reply_markup=markup,
        )
        return True
    except TelegramBadRequest as error:
        if "message is not modified" in str(error).lower():
            return True
        try:
            await bot.edit_message_text(
                chat_id=screen.chat_id,
                message_id=screen.message_id,
                text=text,
                reply_markup=markup,
            )
            return True
        except TelegramBadRequest as nested:
            return "message is not modified" in str(nested).lower()
        except Exception:
            return False
    except Exception:
        return False


async def _edit_media(
    bot: Bot,
    screen: UserScreen,
    text: str,
    markup: InlineKeyboardMarkup | None,
    *,
    image_path: str | Path | None,
    photo_file_id: str | None,
) -> bool:
    try:
        await bot.edit_message_media(
            chat_id=screen.chat_id,
            message_id=screen.message_id,
            media=_desired_media(
                image_path=image_path,
                photo_file_id=photo_file_id,
                caption=text,
            ),
            reply_markup=markup,
        )
        return True
    except TelegramBadRequest as error:
        if "message is not modified" in str(error).lower():
            return True
        return False
    except Exception:
        return False


async def _remove_old_clicked_message(
    bot: Bot,
    source_message: Message | None,
    current_message_id: int,
) -> None:
    if not source_message or source_message.message_id == current_message_id:
        return
    try:
        await bot.delete_message(source_message.chat.id, source_message.message_id)
    except Exception:
        try:
            await bot.edit_message_reply_markup(
                chat_id=source_message.chat.id,
                message_id=source_message.message_id,
                reply_markup=None,
            )
        except Exception:
            pass


async def render_user_screen(
    bot: Bot,
    user_id: int,
    text: str,
    markup: InlineKeyboardMarkup | None = None,
    *,
    source_message: Message | None = None,
    media_key: str = "main",
    image_path: str | Path | None = None,
    photo_file_id: str | None = None,
) -> UserScreen:
    """Render all private bot navigation into one persistent message.

    Existing legacy menu messages are deleted when clicked. A new message is
    created only if the saved screen was deleted or has never existed.
    """
    async with SessionFactory() as session:
        screen = await session.get(UserScreen, user_id)

        if screen is None and source_message is not None:
            candidate = UserScreen(
                user_id=user_id,
                chat_id=source_message.chat.id,
                message_id=source_message.message_id,
                media_key="unknown",
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            changed = await _edit_media(
                bot,
                candidate,
                text,
                markup,
                image_path=image_path,
                photo_file_id=photo_file_id,
            )
            if changed:
                return await register_user_screen(
                    session,
                    user_id,
                    source_message.chat.id,
                    source_message.message_id,
                    media_key=media_key,
                )

        if screen is not None:
            await _remove_old_clicked_message(bot, source_message, screen.message_id)
            changed = False
            if screen.media_key == media_key:
                changed = await _edit_same_media(bot, screen, text, markup)
            if not changed:
                changed = await _edit_media(
                    bot,
                    screen,
                    text,
                    markup,
                    image_path=image_path,
                    photo_file_id=photo_file_id,
                )
            if changed:
                screen.media_key = media_key
                screen.updated_at = datetime.now(timezone.utc)
                await session.commit()
                return screen

        media = _desired_media(
            image_path=image_path,
            photo_file_id=photo_file_id,
            caption=text,
        )
        if isinstance(media.media, FSInputFile):
            message = await bot.send_photo(
                user_id,
                media.media,
                caption=text,
                reply_markup=markup,
                parse_mode="HTML",
            )
        else:
            message = await bot.send_photo(
                user_id,
                media.media,
                caption=text,
                reply_markup=markup,
                parse_mode="HTML",
            )
        if source_message and source_message.message_id != message.message_id:
            await _remove_old_clicked_message(bot, source_message, message.message_id)
        return await register_user_screen(
            session,
            user_id,
            message.chat.id,
            message.message_id,
            media_key=media_key,
        )


async def _delete_later(bot: Bot, chat_id: int, message_id: int, delay: float) -> None:
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass


async def send_ephemeral_notice(
    bot: Bot,
    user_id: int,
    text: str,
    *,
    seconds: float = 18,
) -> None:
    """Create a real push notification without leaving permanent chat spam."""
    try:
        message = await bot.send_message(user_id, text)
    except Exception:
        return
    asyncio.create_task(_delete_later(bot, user_id, message.message_id, seconds))


async def delete_user_input(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass
