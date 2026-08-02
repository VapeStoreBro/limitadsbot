from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.db.session import SessionFactory
from app.enums import OrderStatus, TariffCode
from app.keyboards import moderation_keyboard
from app.keyboards_v3 import moderation_reason_keyboard
from app.models import AdOrder, MiddlePinCandidate
from app.models_extra import OrderDecision
from app.services.app_settings import get_bazaar_chat_id, get_staff_chat_id
from app.services.order_cards import update_buyer_card, update_staff_card

router = Router(name="moderation")

REASON_TEXTS = {
    "text": "Исправьте текст рекламного поста.",
    "links": "Исправьте или уберите ссылки в рекламном посте.",
    "photos": "Замените или исправьте фотографии.",
    "contact": "Свяжитесь с администрацией для уточнения исправлений.",
    "rules": "Заявка нарушает правила размещения рекламы.",
    "category": "Эта реклама не подходит для размещения в барахолке.",
    "forbidden": "В заявке обнаружен запрещённый товар или услуга.",
    "none": "Заявка не прошла модерацию.",
}


async def is_staff_callback(callback: CallbackQuery) -> bool:
    if not callback.message:
        return False
    async with SessionFactory() as session:
        staff_chat_id = await get_staff_chat_id(session)
    return callback.message.chat.id == staff_chat_id


async def save_decision(
    session,
    order_id: int,
    action: str,
    comment: str,
    actor_id: int,
) -> None:
    now = datetime.now(timezone.utc)
    row = await session.get(OrderDecision, order_id)
    if row is None:
        session.add(
            OrderDecision(
                order_id=order_id,
                action=action,
                comment=comment,
                decided_by=actor_id,
                decided_at=now,
            )
        )
    else:
        row.action = action
        row.comment = comment
        row.decided_by = actor_id
        row.decided_at = now


@router.callback_query(F.data.startswith("mod:"))
async def moderation_action(callback: CallbackQuery, bot: Bot) -> None:
    if not await is_staff_callback(callback):
        await callback.answer("Кнопка работает только в группе состава.", show_alert=True)
        return

    _, action, raw_id = callback.data.split(":", 2)
    if action not in {"approve", "revision", "reject"}:
        await callback.answer("В группе состава доступны только три решения.", show_alert=True)
        return

    order_id = int(raw_id)
    if action in {"revision", "reject"}:
        async with SessionFactory() as session:
            order = await session.get(AdOrder, order_id)
            if not order or order.status != OrderStatus.MODERATION.value:
                await callback.answer("Заявку уже обработали или она не найдена.", show_alert=True)
                return
        title = "Причина возврата на исправление" if action == "revision" else "Причина отклонения"
        await callback.message.edit_text(
            f"<b>{title} · заявка №{order_id}</b>\n\n"
            "Выберите комментарий для покупателя.",
            reply_markup=moderation_reason_keyboard(action, order_id),
        )
        await callback.answer("Выберите причину")
        return

    now = datetime.now(timezone.utc)
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
        if not order or order.status != OrderStatus.MODERATION.value:
            await callback.answer("Заявку уже обработали или она не найдена.", show_alert=True)
            return
        order.moderated_by = callback.from_user.id
        order.moderated_at = now
        order.updated_at = now
        order.status = (
            OrderStatus.AWAITING_DEPOSIT.value
            if order.requested_start_at
            else OrderStatus.AWAITING_PAYMENT.value
        )
        await save_decision(
            session,
            order.id,
            "approve",
            "Заявка одобрена администрацией.",
            callback.from_user.id,
        )
        await session.commit()
        await update_buyer_card(session, bot, order)
        await update_staff_card(
            session,
            bot,
            order,
            f"<b>✅ Заявка №{order.id} одобрена</b>\n"
            f"Решение: <b>{callback.from_user.full_name}</b>.\n"
            "Карточка покупателя обновлена, после полной оплаты реклама запустится автоматически.",
        )
    await callback.answer("Одобрено")


@router.callback_query(F.data.startswith("modreason:"))
async def moderation_reason(callback: CallbackQuery, bot: Bot) -> None:
    if not await is_staff_callback(callback):
        await callback.answer("Кнопка работает только в группе состава.", show_alert=True)
        return
    _, action, raw_id, reason_code = callback.data.split(":", 3)
    order_id = int(raw_id)

    if action == "back":
        await callback.message.edit_text(
            f"<b>Решение по заявке №{order_id}</b>\nПроверьте пост и выберите действие.",
            reply_markup=moderation_keyboard(order_id),
        )
        await callback.answer()
        return
    if action not in {"revision", "reject"}:
        await callback.answer("Неизвестное решение.", show_alert=True)
        return

    reason = REASON_TEXTS.get(reason_code, REASON_TEXTS["none"])
    now = datetime.now(timezone.utc)
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
        if not order or order.status != OrderStatus.MODERATION.value:
            await callback.answer("Заявку уже обработали или она не найдена.", show_alert=True)
            return
        order.status = (
            OrderStatus.REVISION.value if action == "revision" else OrderStatus.REJECTED.value
        )
        order.moderated_by = callback.from_user.id
        order.moderated_at = now
        order.updated_at = now
        await save_decision(session, order.id, action, reason, callback.from_user.id)
        await session.commit()
        await update_buyer_card(session, bot, order)
        result = "возвращена на исправление" if action == "revision" else "отклонена"
        await update_staff_card(
            session,
            bot,
            order,
            f"<b>Заявка №{order.id} {result}</b>\n"
            f"Комментарий: <b>{reason}</b>\n"
            f"Решение: <b>{callback.from_user.full_name}</b>.",
        )
    await callback.answer("Карточка покупателя обновлена")


def looks_like_ad_post(message: Message) -> bool:
    if message.reply_to_message or message.text and message.text.startswith("/"):
        return False
    text = (message.caption or message.text or "").strip()
    entity_types = {
        getattr(entity.type, "value", str(entity.type))
        for entity in (message.caption_entities or message.entities or [])
    }
    has_link = bool(entity_types.intersection({"url", "text_link"}))
    return bool(message.photo or len(text) >= 30 or has_link)


@router.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def bazaar_messages(message: Message, bot: Bot) -> None:
    if not message.from_user or not looks_like_ad_post(message):
        return
    async with SessionFactory() as session:
        bazaar_chat_id = await get_bazaar_chat_id(session)
        if message.chat.id != bazaar_chat_id:
            return
        order = await session.scalar(
            select(AdOrder)
            .where(
                AdOrder.user_id == message.from_user.id,
                AdOrder.tariff_code == TariffCode.MIDDLE.value,
                AdOrder.status == OrderStatus.ACTIVE.value,
                AdOrder.awaiting_middle_pin.is_(True),
            )
            .order_by(AdOrder.updated_at.desc(), AdOrder.id.desc())
        )
        if not order:
            return

        existing = await session.get(MiddlePinCandidate, order.id)
        text = (message.caption or message.text or "").strip()
        if existing and message.media_group_id and not text:
            return
        preview = text[:250] if text else "Фотография без подписи"
        now = datetime.now(timezone.utc)
        if existing is None:
            session.add(
                MiddlePinCandidate(
                    order_id=order.id,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    preview_text=preview,
                    created_at=now,
                )
            )
        else:
            existing.chat_id = message.chat.id
            existing.message_id = message.message_id
            existing.preview_text = preview
            existing.created_at = now
        order.updated_at = now
        await session.commit()
        await update_buyer_card(session, bot, order)
