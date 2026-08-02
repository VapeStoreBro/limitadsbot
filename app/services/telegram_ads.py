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
    """Send one advertisement.

    When Best contains several photos and buttons, the first photo becomes the
    main post: it contains the complete caption and inline buttons. The other
    photos follow after it. This lets Telegram pin the actual advertising post
    with its buttons instead of a separate technical message.
    """
    keyboard = best_buttons(order.buttons)
    media = list(order.media or [])
    text = order.content_text or ""

    if not media:
        return [
            await bot.send_message(
                chat_id,
                text or "(пост без текста)",
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        ]

    if len(media) == 1:
        return [
            await bot.send_photo(
                chat_id,
                media[0]["file_id"],
                caption=text or None,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        ]

    if keyboard:
        main = await bot.send_photo(
            chat_id,
            media[0]["file_id"],
            caption=text or None,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        remaining = media[1:]
        if len(remaining) == 1:
            extras = [await bot.send_photo(chat_id, remaining[0]["file_id"])]
        else:
            extras = list(
                await bot.send_media_group(
                    chat_id,
                    media=[InputMediaPhoto(media=item["file_id"]) for item in remaining],
                )
            )
        return [main, *extras]

    album = [
        InputMediaPhoto(
            media=item["file_id"],
            caption=text if index == 0 else None,
            parse_mode="HTML",
        )
        for index, item in enumerate(media)
    ]
    return list(await bot.send_media_group(chat_id, media=album))


async def refresh_user_prefix(session: AsyncSession, bot: Bot, user_id: int) -> None:
    settings = get_settings()
    active = (
        await session.scalars(
            select(AdOrder).where(
                AdOrder.user_id == user_id,
                AdOrder.status == OrderStatus.ACTIVE.value,
                AdOrder.ends_at.is_not(None),
            )
        )
    ).all()
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


async def activate_order(
    session: AsyncSession,
    bot: Bot,
    order: AdOrder,
    actor_id: int,
) -> None:
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
        main = messages[0]
        await bot.pin_chat_message(
            settings.bazaar_chat_id,
            main.message_id,
            disable_notification=True,
        )
        order.pinned_message_id = main.message_id
        order.next_publish_at = now + timedelta(hours=3)
        for message in messages:
            session.add(
                Publication(
                    order_id=order.id,
                    chat_id=settings.bazaar_chat_id,
                    message_id=message.message_id,
                    kind=PublicationKind.MAIN.value,
                    created_at=now,
                    deleted_at=None,
                )
            )

    await session.commit()
    await refresh_user_prefix(session, bot, order.user_id)


async def capture_middle_pin(
    session: AsyncSession,
    bot: Bot,
    order: AdOrder,
    message: Message,
) -> None:
    settings = get_settings()
    replacing_existing = bool(order.pinned_message_id)
    old_message_id = order.pinned_message_id

    await bot.pin_chat_message(
        settings.bazaar_chat_id,
        message.message_id,
        disable_notification=True,
    )
    if old_message_id and old_message_id != message.message_id:
        try:
            await bot.unpin_chat_message(settings.bazaar_chat_id, old_message_id)
        except Exception:
            pass

    order.pinned_message_id = message.message_id
    order.awaiting_middle_pin = False
    if replacing_existing:
        order.pin_changes_used += 1
    order.updated_at = datetime.now(timezone.utc)
    session.add(
        Publication(
            order_id=order.id,
            chat_id=settings.bazaar_chat_id,
            message_id=message.message_id,
            kind=PublicationKind.MIDDLE_PIN.value,
            created_at=datetime.now(timezone.utc),
            deleted_at=None,
        )
    )
    await session.commit()


async def finish_order(
    session: AsyncSession,
    bot: Bot,
    order: AdOrder,
    *,
    status: str = OrderStatus.COMPLETED.value,
) -> None:
    settings = get_settings()
    if order.pinned_message_id:
        try:
            await bot.unpin_chat_message(
                settings.bazaar_chat_id,
                order.pinned_message_id,
            )
        except Exception:
            pass
    order.status = status
    order.awaiting_middle_pin = False
    order.next_publish_at = None
    order.pinned_message_id = None
    order.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await refresh_user_prefix(session, bot, order.user_id)


async def publish_best_copy(
    session: AsyncSession,
    bot: Bot,
    order: AdOrder,
) -> list[Message]:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    messages = await send_ad_content(bot, settings.bazaar_chat_id, order)
    for message in messages:
        session.add(
            Publication(
                order_id=order.id,
                chat_id=settings.bazaar_chat_id,
                message_id=message.message_id,
                kind=PublicationKind.COPY.value,
                created_at=now,
                deleted_at=None,
            )
        )
    order.next_publish_at = now + timedelta(hours=3)
    await session.flush()

    copies = (
        await session.scalars(
            select(Publication)
            .where(
                Publication.order_id == order.id,
                Publication.kind == PublicationKind.COPY.value,
                Publication.deleted_at.is_(None),
            )
            .order_by(Publication.created_at.desc(), Publication.id.desc())
        )
    ).all()

    grouped: list[list[Publication]] = []
    for publication in copies:
        if (
            not grouped
            or abs(
                (grouped[-1][0].created_at - publication.created_at).total_seconds()
            )
            > 2
        ):
            grouped.append([publication])
        else:
            grouped[-1].append(publication)

    for old_group in grouped[3:]:
        for publication in old_group:
            try:
                await bot.delete_message(publication.chat_id, publication.message_id)
            except Exception:
                pass
            publication.deleted_at = now

    await session.commit()
    return messages
