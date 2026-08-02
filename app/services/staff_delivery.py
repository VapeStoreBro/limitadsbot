from __future__ import annotations

import re
from html import escape, unescape
from types import SimpleNamespace

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputMediaPhoto, Message

from app.config import get_settings
from app.db.session import SessionFactory
from app.keyboards import DURATION_NAMES, TARIFF_NAMES, moderation_keyboard
from app.models import AdOrder, User
from app.services.app_settings import get_staff_chat_id
from app.services.telegram_ads import send_ad_content

_TAG_RE = re.compile(r"<[^>]+>")
settings = get_settings()


def plain_text(value: str) -> str:
    return unescape(_TAG_RE.sub("", value or "")).strip()


def staff_order_text(order: AdOrder, user: User) -> str:
    full_name = " ".join(
        part for part in (user.first_name, user.last_name) if part
    ).strip() or str(user.id)
    username = f"@{escape(user.username)}" if user.username else "не указан"
    phone = escape(user.phone or "не указан")
    booking = escape(str(order.requested_start_at or "нет"))
    return (
        f"<b><u>🛡 НОВАЯ ЗАЯВКА №{order.id}</u></b>\n\n"
        f"<b>Покупатель</b>\n"
        f"├ Имя: <a href=\"tg://user?id={user.id}\">{escape(full_name)}</a>\n"
        f"├ ID: <code>{user.id}</code>\n"
        f"├ Username: {username}\n"
        f"└ Телефон: <code>{phone}</code>\n\n"
        f"<b>Реклама</b>\n"
        f"├ Тариф: <b>{TARIFF_NAMES.get(order.tariff_code, order.tariff_code)}</b>\n"
        f"├ Срок: <b>{DURATION_NAMES.get(order.duration_code, order.duration_code)}</b>\n"
        f"├ Стоимость: <b>{order.price_rub} ₽</b>\n"
        f"└ Бронирование: <b>{booking}</b>\n\n"
        "<i>В группе стафа доступны только три решения: одобрить, вернуть на исправление или отклонить.</i>"
    )


async def send_ad_content_resilient(
    bot: Bot,
    chat_id: int,
    order: AdOrder | SimpleNamespace,
) -> list[Message]:
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


async def current_staff_chat_id() -> int:
    async with SessionFactory() as session:
        return await get_staff_chat_id(session)


async def deliver_order_to_staff(bot: Bot, order: AdOrder, user: User) -> int:
    staff_chat_id = await current_staff_chat_id()
    await bot.send_message(staff_chat_id, staff_order_text(order, user))
    await send_ad_content_resilient(bot, staff_chat_id, order)
    card = await bot.send_message(
        staff_chat_id,
        (
            f"<b>Решение по заявке №{order.id}</b>\n"
            "Проверьте пост и выберите одно из трёх действий."
        ),
        reply_markup=moderation_keyboard(order.id),
    )
    return card.message_id


async def test_staff_chat(bot: Bot) -> tuple[bool, str, int]:
    staff_chat_id = await current_staff_chat_id()
    try:
        chat = await bot.get_chat(staff_chat_id)
        me = await bot.get_me()
        member = await bot.get_chat_member(staff_chat_id, me.id)
        probe = await bot.send_message(
            staff_chat_id,
            "🧪 Проверка связи с системой рекламы прошла успешно.",
        )
        try:
            await bot.delete_message(staff_chat_id, probe.message_id)
        except Exception:
            pass
        title = getattr(chat, "title", None) or str(staff_chat_id)
        return True, f"Группа: {title}; статус бота: {member.status}", staff_chat_id
    except Exception as error:
        return False, f"{type(error).__name__}: {error}", staff_chat_id


async def notify_delivery_failure(bot: Bot, order_id: int, error: Exception) -> None:
    try:
        staff_chat_id = await current_staff_chat_id()
        error_text = escape(f"{type(error).__name__}: {error}")
        await bot.send_message(
            settings.owner_id,
            (
                f"<b>⚠️ Заявка №{order_id} сохранена, но не доставлена в группу стафа.</b>\n\n"
                f"Текущий ID группы: <code>{staff_chat_id}</code>\n"
                f"Ошибка Telegram: <code>{error_text}</code>\n\n"
                "Откройте: Админ-панель → Настройки → Проверить группу стафа."
            ),
        )
    except Exception:
        pass
