from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InputMediaPhoto, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionFactory
from app.models import UserScreen
from app.services.price_card import ensure_main_menu_card, ensure_price_card

_SCREEN_LOCKS: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
_ASSET_FILE_IDS: dict[str, str] = {}


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


def _asset_source(
    *,
    media_key: str,
    image_path: str | Path | None,
    photo_file_id: str | None,
):
    if photo_file_id:
        return photo_file_id
    cached = _ASSET_FILE_IDS.get(media_key)
    if cached:
        return cached
    selected = image_path or ensure_main_menu_card() or ensure_price_card()
    return FSInputFile(Path(selected))


def _desired_media(
    *,
    media_key: str,
    image_path: str | Path | None,
    photo_file_id: str | None,
    caption: str,
) -> InputMediaPhoto:
    return InputMediaPhoto(
        media=_asset_source(
            media_key=media_key,
            image_path=image_path,
            photo_file_id=photo_file_id,
        ),
        caption=caption,
        parse_mode="HTML",
    )


def _remember_photo(media_key: str, message: Message | None) -> None:
    if message and message.photo:
        _ASSET_FILE_IDS[media_key] = message.photo[-1].file_id


async def _edit_photo_caption(
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
        return "message is not modified" in str(error).lower()
    except Exception:
        return False


async def _edit_text(
    bot: Bot,
    screen: UserScreen,
    text: str,
    markup: InlineKeyboardMarkup | None,
) -> bool:
    try:
        await bot.edit_message_text(
            chat_id=screen.chat_id,
            message_id=screen.message_id,
            text=text,
            reply_markup=markup,
        )
        return True
    except TelegramBadRequest as error:
        return "message is not modified" in str(error).lower()
    except Exception:
        return False


async def _edit_media(
    bot: Bot,
    screen: UserScreen,
    text: str,
    markup: InlineKeyboardMarkup | None,
    *,
    media_key: str,
    image_path: str | Path | None,
    photo_file_id: str | None,
) -> bool:
    try:
        edited = await bot.edit_message_media(
            chat_id=screen.chat_id,
            message_id=screen.message_id,
            media=_desired_media(
                media_key=media_key,
                image_path=image_path,
                photo_file_id=photo_file_id,
                caption=text,
            ),
            reply_markup=markup,
        )
        _remember_photo(media_key, edited)
        return True
    except TelegramBadRequest as error:
        return "message is not modified" in str(error).lower()
    except Exception:
        return False


async def _delete_message(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        try:
            await bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=None,
            )
        except Exception:
            pass


async def _remove_old_clicked_message(
    bot: Bot,
    source_message: Message | None,
    current_message_id: int,
) -> None:
    if not source_message or source_message.message_id == current_message_id:
        return
    await _delete_message(bot, source_message.chat.id, source_message.message_id)


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
    text_only: bool = False,
) -> UserScreen:
    """Render private navigation into one current message.

    Photo screens are edited in place. A genuine text-only screen is used for
    posts without photographs; when returning to a menu the text message is
    deleted and the branded menu image is restored. A per-user lock prevents
    simultaneous callbacks from creating duplicate screens.
    """
    auto_text_preview = (
        photo_file_id is None
        and image_path is None
        and text.lstrip().startswith("<b><u>👁")
    )
    want_text = text_only or auto_text_preview
    target_key = f"text:{media_key}" if want_text else media_key

    async with _SCREEN_LOCKS[user_id]:
        async with SessionFactory() as session:
            screen = await session.get(UserScreen, user_id)

            if screen is not None:
                await _remove_old_clicked_message(bot, source_message, screen.message_id)
                current_is_text = screen.media_key.startswith("text:")
                changed = False

                if want_text and current_is_text:
                    changed = await _edit_text(bot, screen, text, markup)
                elif not want_text and not current_is_text:
                    if screen.media_key == target_key:
                        changed = await _edit_photo_caption(bot, screen, text, markup)
                    if not changed:
                        changed = await _edit_media(
                            bot,
                            screen,
                            text,
                            markup,
                            media_key=target_key,
                            image_path=image_path,
                            photo_file_id=photo_file_id,
                        )

                if changed:
                    screen.media_key = target_key
                    screen.updated_at = datetime.now(timezone.utc)
                    await session.commit()
                    return screen

                # Telegram cannot convert a media message to text or text to
                # media. Remove the previous current screen before replacing it.
                await _delete_message(bot, screen.chat_id, screen.message_id)

            elif source_message is not None:
                # Reuse an existing text message only when the requested screen
                # is also text-only. Media conversion is handled by replacement.
                candidate = UserScreen(
                    user_id=user_id,
                    chat_id=source_message.chat.id,
                    message_id=source_message.message_id,
                    media_key="text:unknown" if not source_message.photo else "unknown",
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                if want_text and not source_message.photo:
                    if await _edit_text(bot, candidate, text, markup):
                        return await register_user_screen(
                            session,
                            user_id,
                            source_message.chat.id,
                            source_message.message_id,
                            media_key=target_key,
                        )

            if want_text:
                message = await bot.send_message(user_id, text, reply_markup=markup)
            else:
                media = _desired_media(
                    media_key=target_key,
                    image_path=image_path,
                    photo_file_id=photo_file_id,
                    caption=text,
                )
                message = await bot.send_photo(
                    user_id,
                    media.media,
                    caption=text,
                    reply_markup=markup,
                    parse_mode="HTML",
                )
                _remember_photo(target_key, message)

            if source_message and source_message.message_id != message.message_id:
                await _remove_old_clicked_message(bot, source_message, message.message_id)
            return await register_user_screen(
                session,
                user_id,
                message.chat.id,
                message.message_id,
                media_key=target_key,
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
