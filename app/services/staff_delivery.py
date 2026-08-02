from __future__ import annotations

import re
from html import unescape
from types import SimpleNamespace

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputMediaPhoto, Message

from app.config import get_settings
from app.keyboards import DURATION_NAMES, TARIFF_NAMES, moderation_keyboard
from app.models import AdOrder, User
from app.services.telegram_ads import send_ad_content

_TAG_RE = re.compile(r"<[^>]+>")
settings = get_settings()


def plain_text(value: str) -> str:
    """Create a readable fallback when Telegram rejects rich HTML entities."""
    return unescape(_TAG_RE.sub("", value or "")).strip()


def staff_order_text(order: AdOrder, user: User) -> str:
    full_name = " ".join(
        part for part in (user.first_name, user.last_name) if part
    ).strip() or str(user.id)
    username = f"@{user.username}" if user.username else "не указан"
    phone = user.phone or "не указан"
    booking = order.requested_start_at or "нет"
    return (
        f"<b><u>🛡 НОВАЯ ЗАЯВКА №{order.id}</u></b>\n\n"
        f"<b>Покупатель</b>\n"
        f"├ Имя: <a href=\"tg://user?id={user.id}\">{full_name}</a>\n"
        f"├ ID: <code>{user.id}</code>\n"
        f"├ Username: {username}\n"
        f"└ Телефон: <code>{phone}</code>\n\n"
        f"<b>Реклама</b>\n"
        f"├ Тариф: <b>{TARIFF_NAMES.get(order.tariff_code, order.tariff_code)}</b>\n"
        f"├ Срок: <b>{DURATION_NAMES.get(order.duration_code, order.duration_code)}</b>\n"
        f"├ Стоимость: <b>{order.price_rub} ₽</b>\n"
        f"└ Бронирование: <b>{booking}</b>\n\n"
        "<i>Ниже отправлен точный предпросмотр рекламного поста.</i>"
    )


async def send_ad_content_resilient(
    bot: Bot,
    chat_id: int,
    order: AdOrder | SimpleNamespace,
) -> list[Message]:
    """Send formatted content and retry without HTML if Telegram rejects entities."""
    try:
        return await send_ad_content(bot, chat_id, order)
    except TelegramBadRequest:
        media = list(order.media or [])
        text = plain_text(order.content_text or "")
        if not media:
            return [
                await bot.send_message(
                    chat_id,
                    text or "(пост без текста)",
                    parse_mode=None,
                )
            ]
        if len(media) == 1:
            return [
                await bot.send_photo(
                    chat_id,
                    media[0]["file_id"],
                    caption=text[:1024] or None,
                    parse_mode=None,
                )
            ]
        album = [
            InputMediaPhoto(
                media=item["file_id"],
                caption=text[:1024] if index == 0 and text else None,
                parse_mode=None,
            )
            for index, item in enumerate(media)
        ]
        return list(await bot.send_media_group(chat_id, media=album))


async def deliver_order_to_staff(bot: Bot, order: AdOrder, user: User) -> int:
    """Deliver a complete moderation package and return the action-card message id."""
    await bot.send_message(settings.staff_chat_id, staff_order_text(order, user))
    await send_ad_content_resilient(bot, settings.staff_chat_id, order)
    card = await bot.send_message(
        settings.staff_chat_id,
        (
            f"<b>Решение по заявке №{order.id}</b>\n"
            "Проверьте содержание поста и выберите действие."
        ),
        reply_markup=moderation_keyboard(order.id),
    )
    return card.message_id


async def notify_delivery_failure(bot: Bot, order_id: int, error: Exception) -> None:
    try:
        await bot.send_message(
            settings.owner_id,
            (
                f"<b>⚠️ Заявка №{order_id} сохранена, но не доставлена в группу стафа.</b>\n\n"
                f"Ошибка: <code>{type(error).__name__}: {error}</code>\n\n"
                "Заявка доступна в админ-панели → 📥 Заявки."
            ),
        )
    except Exception:
        pass
