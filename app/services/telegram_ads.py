import asyncio
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.types import InputMediaPhoto, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.enums import OrderStatus, PublicationKind, TariffCode
from app.keyboards import best_buttons
from app.models import AdOrder, MiddlePinCandidate, Publication
from app.rules import advertising_prefix
from app.services.app_settings import get_bazaar_chat_id


async def send_ad_content(bot: Bot, chat_id: int, order: AdOrder) -> list[Message]:
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
    bazaar_chat_id = await get_bazaar_chat_id(session)
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
        try:
            await bot.promote_chat_member(
                bazaar_chat_id,
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
        except Exception:
            pass
        return

    furthest_end = max(order.ends_at for order in active if order.ends_at is not None)
    try:
        await bot.promote_chat_member(
            bazaar_chat_id,
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
            bazaar_chat_id,
            user_id,
            advertising_prefix(furthest_end, settings.timezone),
        )
    except Exception:
        pass


async def refresh_bot_prefix(session: AsyncSession, bot: Bot) -> None:
    """Show the same advertising date on the bot while a Best is active."""
    settings = get_settings()
    bazaar_chat_id = await get_bazaar_chat_id(session)
    active_best = (
        await session.scalars(
            select(AdOrder).where(
                AdOrder.tariff_code == TariffCode.BEST.value,
                AdOrder.status == OrderStatus.ACTIVE.value,
                AdOrder.ends_at.is_not(None),
            )
        )
    ).all()
    try:
        me = await bot.get_me()
        title = (
            advertising_prefix(
                max(order.ends_at for order in active_best if order.ends_at),
                settings.timezone,
            )
            if active_best
            else "Limit Ads"
        )
        await bot.set_chat_administrator_custom_title(
            bazaar_chat_id,
            me.id,
            title,
        )
    except Exception:
        # Telegram may forbid changing the bot's own title depending on who
        # promoted it. This must never stop advertising automation.
        pass


async def _record_publications(
    session: AsyncSession,
    order: AdOrder,
    messages: list[Message],
    kind: str,
) -> None:
    now = datetime.now(timezone.utc)
    for message in messages:
        session.add(
            Publication(
                order_id=order.id,
                chat_id=message.chat.id,
                message_id=message.message_id,
                kind=kind,
                created_at=now,
                deleted_at=None,
            )
        )


async def _unpin_message(bot: Bot, chat_id: int, message_id: int | None) -> None:
    if not message_id:
        return
    try:
        await bot.unpin_chat_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def suspend_best_pin_for_middle(
    session: AsyncSession,
    bot: Bot,
) -> None:
    """Give a new Middle post the top position, then schedule Best in 2 minutes."""
    bazaar_chat_id = await get_bazaar_chat_id(session)
    now = datetime.now(timezone.utc)
    best_orders = (
        await session.scalars(
            select(AdOrder).where(
                AdOrder.tariff_code == TariffCode.BEST.value,
                AdOrder.status == OrderStatus.ACTIVE.value,
            )
        )
    ).all()
    for best in best_orders:
        await _unpin_message(bot, bazaar_chat_id, best.pinned_message_id)
        best.pinned_message_id = None
        # Do not wait for the normal three-hour cycle: the next scheduler pass
        # after this timestamp publishes a fresh Best and places it on top.
        best.next_publish_at = now + timedelta(minutes=2)
        best.updated_at = now


async def pin_best_as_newest(
    session: AsyncSession,
    bot: Bot,
    order: AdOrder,
    message_id: int,
) -> None:
    """Keep one current Best pin while preserving all Middle pins."""
    bazaar_chat_id = await get_bazaar_chat_id(session)
    other_best = (
        await session.scalars(
            select(AdOrder).where(
                AdOrder.tariff_code == TariffCode.BEST.value,
                AdOrder.status == OrderStatus.ACTIVE.value,
                AdOrder.pinned_message_id.is_not(None),
            )
        )
    ).all()
    for best in other_best:
        if best.pinned_message_id != message_id:
            await _unpin_message(bot, bazaar_chat_id, best.pinned_message_id)
        if best.id != order.id:
            best.pinned_message_id = None
            best.updated_at = datetime.now(timezone.utc)

    await bot.pin_chat_message(
        bazaar_chat_id,
        message_id,
        disable_notification=True,
    )
    order.pinned_message_id = message_id
    order.updated_at = datetime.now(timezone.utc)


async def restore_order_pin(
    session: AsyncSession,
    bot: Bot,
    order: AdOrder,
) -> bool:
    if not order.pinned_message_id:
        return False
    bazaar_chat_id = await get_bazaar_chat_id(session)
    await bot.pin_chat_message(
        bazaar_chat_id,
        order.pinned_message_id,
        disable_notification=True,
    )
    if order.tariff_code == TariffCode.MIDDLE.value:
        await suspend_best_pin_for_middle(session, bot)
    elif order.tariff_code == TariffCode.BEST.value:
        await pin_best_as_newest(session, bot, order, order.pinned_message_id)
    await session.commit()
    return True


async def activate_order(
    session: AsyncSession,
    bot: Bot,
    order: AdOrder,
    actor_id: int = 0,
) -> bool:
    """Activate once. Repeated calls are safe and never reset the timer."""
    if order.status == OrderStatus.ACTIVE.value:
        return True
    if order.paid_rub < order.price_rub:
        return False

    now = datetime.now(timezone.utc)
    if order.requested_start_at and order.requested_start_at > now:
        order.status = OrderStatus.BOOKED.value
        order.updated_at = now
        await session.commit()
        return False

    bazaar_chat_id = await get_bazaar_chat_id(session)
    messages: list[Message] = []
    if order.tariff_code == TariffCode.BEST.value:
        messages = await send_ad_content(bot, bazaar_chat_id, order)
        await pin_best_as_newest(session, bot, order, messages[0].message_id)
        order.next_publish_at = now + timedelta(hours=3)
        await _record_publications(session, order, messages, PublicationKind.MAIN.value)
    elif order.tariff_code == TariffCode.MIDDLE.value:
        order.awaiting_middle_pin = True

    order.status = OrderStatus.ACTIVE.value
    order.activated_by = actor_id or order.moderated_by or 0
    order.activated_at = now
    order.ends_at = now + timedelta(hours=order.duration_hours)
    order.requested_start_at = now
    order.requested_end_at = order.ends_at
    order.updated_at = now
    await session.commit()
    await refresh_user_prefix(session, bot, order.user_id)
    if order.tariff_code == TariffCode.BEST.value:
        await refresh_bot_prefix(session, bot)
    return True


async def replace_best_publication(
    session: AsyncSession,
    bot: Bot,
    order: AdOrder,
) -> None:
    """Publish the edited Best post and promote it without touching Middle pins."""
    if order.tariff_code != TariffCode.BEST.value or order.status != OrderStatus.ACTIVE.value:
        await session.commit()
        return

    bazaar_chat_id = await get_bazaar_chat_id(session)
    now = datetime.now(timezone.utc)
    old_main = (
        await session.scalars(
            select(Publication).where(
                Publication.order_id == order.id,
                Publication.kind == PublicationKind.MAIN.value,
                Publication.deleted_at.is_(None),
            )
        )
    ).all()
    old_ids = {publication.message_id for publication in old_main}
    if order.pinned_message_id:
        old_ids.add(order.pinned_message_id)

    messages = await send_ad_content(bot, bazaar_chat_id, order)
    new_main = messages[0]
    await pin_best_as_newest(session, bot, order, new_main.message_id)

    for old_id in old_ids:
        if old_id == new_main.message_id:
            continue
        await _unpin_message(bot, bazaar_chat_id, old_id)
        try:
            await bot.delete_message(bazaar_chat_id, old_id)
        except Exception:
            pass

    for publication in old_main:
        publication.deleted_at = now
    await _record_publications(session, order, messages, PublicationKind.MAIN.value)
    order.updated_at = now
    await session.commit()


async def confirm_middle_pin(
    session: AsyncSession,
    bot: Bot,
    order: AdOrder,
    candidate: MiddlePinCandidate,
) -> None:
    bazaar_chat_id = await get_bazaar_chat_id(session)
    new_message_id = candidate.message_id
    replacing_existing = bool(order.pinned_message_id)

    previous = (
        await session.scalars(
            select(Publication).where(
                Publication.order_id == order.id,
                Publication.kind == PublicationKind.MIDDLE_PIN.value,
                Publication.deleted_at.is_(None),
            )
        )
    ).all()
    old_ids = {publication.message_id for publication in previous}
    if order.pinned_message_id:
        old_ids.add(order.pinned_message_id)

    await bot.pin_chat_message(
        chat_id=bazaar_chat_id,
        message_id=new_message_id,
        disable_notification=True,
    )

    for old_id in old_ids:
        if old_id == new_message_id:
            continue
        for _ in range(2):
            try:
                await bot.unpin_chat_message(
                    chat_id=bazaar_chat_id,
                    message_id=old_id,
                )
                break
            except Exception:
                await asyncio.sleep(0.2)

    # Middle is immediately on top. Active Best is returned automatically in
    # about two minutes by publishing a fresh copy, not by reusing an old post.
    await suspend_best_pin_for_middle(session, bot)

    now = datetime.now(timezone.utc)
    for publication in previous:
        if publication.message_id != new_message_id:
            publication.deleted_at = now
    order.pinned_message_id = new_message_id
    order.awaiting_middle_pin = False
    if replacing_existing:
        order.pin_changes_used += 1
    order.updated_at = now
    session.add(
        Publication(
            order_id=order.id,
            chat_id=bazaar_chat_id,
            message_id=new_message_id,
            kind=PublicationKind.MIDDLE_PIN.value,
            created_at=now,
            deleted_at=None,
        )
    )
    await session.delete(candidate)
    await session.commit()


async def capture_middle_pin(
    session: AsyncSession,
    bot: Bot,
    order: AdOrder,
    message: Message,
) -> None:
    candidate = MiddlePinCandidate(
        order_id=order.id,
        chat_id=message.chat.id,
        message_id=message.message_id,
        preview_text=(message.caption or message.text or "")[:255],
        created_at=datetime.now(timezone.utc),
    )
    await confirm_middle_pin(session, bot, order, candidate)


async def finish_order(
    session: AsyncSession,
    bot: Bot,
    order: AdOrder,
    *,
    status: str = OrderStatus.COMPLETED.value,
) -> None:
    bazaar_chat_id = await get_bazaar_chat_id(session)

    # Never trust only one saved ID. Unpin every publication belonging to the
    # order so voluntary stop, admin stop and scheduled finish behave equally.
    publications = (
        await session.scalars(
            select(Publication).where(
                Publication.order_id == order.id,
                Publication.deleted_at.is_(None),
            )
        )
    ).all()
    pin_ids = {publication.message_id for publication in publications}
    if order.pinned_message_id:
        pin_ids.add(order.pinned_message_id)
    for message_id in pin_ids:
        await _unpin_message(bot, bazaar_chat_id, message_id)

    candidate = await session.get(MiddlePinCandidate, order.id)
    if candidate:
        await session.delete(candidate)
    order.status = status
    order.awaiting_middle_pin = False
    order.next_publish_at = None
    order.pinned_message_id = None
    order.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await refresh_user_prefix(session, bot, order.user_id)
    if order.tariff_code == TariffCode.BEST.value:
        await refresh_bot_prefix(session, bot)


async def publish_best_copy(
    session: AsyncSession,
    bot: Bot,
    order: AdOrder,
) -> list[Message]:
    bazaar_chat_id = await get_bazaar_chat_id(session)
    now = datetime.now(timezone.utc)
    messages = await send_ad_content(bot, bazaar_chat_id, order)
    await _record_publications(session, order, messages, PublicationKind.COPY.value)

    await pin_best_as_newest(session, bot, order, messages[0].message_id)
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
            or abs((grouped[-1][0].created_at - publication.created_at).total_seconds()) > 2
        ):
            grouped.append([publication])
        else:
            grouped[-1].append(publication)

    for old_group in grouped[3:]:
        for publication in old_group:
            if publication.message_id == order.pinned_message_id:
                continue
            try:
                await bot.delete_message(publication.chat_id, publication.message_id)
            except Exception:
                pass
            publication.deleted_at = now

    await session.commit()
    return messages
