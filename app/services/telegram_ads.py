from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.types import InputMediaPhoto, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.enums import OrderStatus, PublicationKind, TariffCode
from app.keyboards import best_buttons
from app.models import AdOrder, Publication
from app.rules import advertising_prefix


async def send_ad_content(bot: Bot, chat_id: int, order: AdOrder) -> list[Message]:
    keyboard = best_buttons(order.buttons)
    media = list(order.media or [])
    text = order.content_text or ""
    if not media:
        return [await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")]
    if len(media) == 1:
        return [await bot.send_photo(chat_id, media[0]["file_id"], caption=text or None, reply_markup=keyboard, parse_mode="HTML")]
    album = [InputMediaPhoto(media=item["file_id"], caption=text if index == 0 else None, parse_mode="HTML") for index, item in enumerate(media)]
    messages = list(await bot.send_media_group(chat_id, media=album))
    if keyboard:
        companion = await bot.send_message(
            chat_id,
            "🔗 Ссылки рекламного объявления",
            reply_markup=keyboard,
            reply_to_message_id=messages[0].message_id,
        )
        messages.append(companion)
    return messages


async def refresh_user_prefix(session: AsyncSession, bot: Bot, user_id: int) -> None:
    settings = get_settings()
    active = (await session.scalars(select(AdOrder).where(
        AdOrder.user_id == user_id,
        AdOrder.status == OrderStatus.ACTIVE.value,
        AdOrder.ends_at.is_not(None),
    ))).all()
    if not active:
        await bot.promote_chat_member(
            settings.bazaar_chat_id,
            user_id,
            can_manage_chat=False,
            can_delete_messages=False,
            can_manage_video_chats=False,
            can_restrict_members=False,
            can_promote_members=False,
            can_change_info=False,
            can_invite_users=False,
            can_pin_messages=False,
        )
        return
    furthest_end = max(order.ends_at for order in active if order.ends_at is not None)
    await bot.promote_chat_member(
        settings.bazaar_chat_id,
        user_id,
        can_manage_chat=True,
        can_delete_messages=False,
        can_manage_video_chats=False,
        can_restrict_members=False,
        can_promote_members=False,
        can_change_info=False,
        can_invite_users=False,
        can_pin_messages=False,
    )
    await bot.set_chat_administrator_custom_title(
        settings.bazaar_chat_id,
        user_id,
        advertising_prefix(furthest_end, settings.timezone),
    )


async def activate_order(session: AsyncSession, bot: Bot, order: AdOrder, actor_id: int) -> None:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    order.status = OrderStatus.ACTIVE.value
    order.activated_by = actor_id
    order.activated_at = now
    order.ends_at = now + timedelta(hours=order.duration_hours)
    order.requested_start_at = now
    order.requested_end_at = order.ends_at
    order.updated_at = now
    if order.tariff_code == TariffCode.MIDDLE.value:
        order.awaiting_middle_pin = True
    elif order.tariff_code == TariffCode.BEST.value:
        messages = await send_ad_content(bot, settings.bazaar_chat_id, order)
        main = messages[-1] if order.buttons and len(order.media or []) > 1 else messages[0]
        await bot.pin_chat_message(settings.bazaar_chat_id, main.message_id, disable_notification=True)
        order.pinned_message_id = main.message_id
        order.next_publish_at = now + timedelta(hours=3)
        for message in messages:
            session.add(Publication(
                order_id=order.id,
                chat_id=settings.bazaar_chat_id,
                message_id=message.message_id,
                kind=PublicationKind.MAIN.value,
                created_at=now,
                deleted_at=None,
            ))
    await session.commit()
    await refresh_user_prefix(session, bot, order.user_id)


async def capture_middle_pin(session: AsyncSession, bot: Bot, order: AdOrder, message: Message) -> None:
    settings = get_settings()
    replacing_existing = bool(order.pinned_message_id)
    if order.pinned_message_id:
        try:
            await bot.unpin_chat_message(settings.bazaar_chat_id, order.pinned_message_id)
        except Exception:
            pass
    await bot.pin_chat_message(settings.bazaar_chat_id, message.message_id, disable_notification=True)
    order.pinned_message_id = message.message_id
    order.awaiting_middle_pin = False
    if replacing_existing:
        order.pin_changes_used += 1
    order.updated_at = datetime.now(timezone.utc)
    session.add(Publication(
        order_id=order.id,
        chat_id=settings.bazaar_chat_id,
        message_id=message.message_id,
        kind=PublicationKind.MIDDLE_PIN.value,
        created_at=datetime.now(timezone.utc),
        deleted_at=None,
    ))
    await session.commit()
