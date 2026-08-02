from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from app.db.session import SessionFactory
from app.enums import OrderStatus
from app.models import AdOrder, User
from app.models_extra import OrderDecision
from app.rules import validate_post
from app.services.media_groups import MediaGroupCollector
from app.services.order_cards import update_buyer_card, update_staff_card
from app.services.staff_delivery import deliver_order_to_staff, notify_delivery_failure
from app.services.ui_screen import delete_user_input, render_user_screen
from app.states import RevisionFlow

router = Router(name="revision_v9")
collector = MediaGroupCollector()


def nav(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ К заказу", callback_data=f"buyerorder:view:{order_id}")],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
    ])


@router.callback_query(F.data.startswith("revisionv9:replace:"))
async def start_replace(callback: CallbackQuery, state: FSMContext) -> None:
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.scalar(select(AdOrder).where(
            AdOrder.id == order_id,
            AdOrder.user_id == callback.from_user.id,
            AdOrder.status == OrderStatus.REVISION.value,
        ))
    if not order:
        await callback.answer("Заказ уже не находится на исправлении.", show_alert=True)
        return
    await state.set_state(RevisionFlow.waiting_post)
    await state.update_data(revision_order_id=order.id)
    await render_user_screen(
        callback.bot,
        callback.from_user.id,
        f"<b><u>✏️ ИСПРАВЛЕНИЕ ЗАЯВКИ №{order.id}</u></b>\n\n"
        "Отправьте новый готовый пост: текст, одну фотографию с подписью или альбом до 8 фотографий.",
        nav(order.id),
        source_message=callback.message,
        media_key="revision",
        text_only=True,
    )
    await callback.answer("Жду исправленный пост")


def message_text(message: Message) -> tuple[str, str]:
    plain = message.caption if message.caption is not None else message.text or ""
    try:
        html = message.html_caption if message.caption is not None else message.html_text
    except Exception:
        html = None
    return plain, html or escape(plain)


async def process_replacement(messages: list[Message], state: FSMContext, bot: Bot) -> None:
    first = messages[0]
    data = await state.get_data()
    order_id = int(data.get("revision_order_id", 0))
    if not order_id or not first.from_user:
        await state.clear()
        return
    source = next((m for m in messages if m.caption is not None or m.text is not None), first)
    plain, html = message_text(source)
    media: list[dict[str, str]] = []
    for item in messages:
        if item.photo:
            media.append({"type": "photo", "file_id": item.photo[-1].file_id})
        elif item.text is None:
            await render_user_screen(bot, first.from_user.id,
                "<b>❌ Формат не подходит</b>\n\nРазрешены только текст и фотографии.",
                nav(order_id), media_key="revision", text_only=True)
            return
    if len(media) > 8:
        await render_user_screen(bot, first.from_user.id,
            "<b>❌ Максимум 8 фотографий</b>", nav(order_id),
            media_key="revision", text_only=True)
        return
    async with SessionFactory() as session:
        order = await session.scalar(select(AdOrder).where(
            AdOrder.id == order_id,
            AdOrder.user_id == first.from_user.id,
            AdOrder.status == OrderStatus.REVISION.value,
        ).with_for_update())
        if not order:
            await state.clear()
            return
        result = validate_post(order.tariff_code, plain, media, list(order.buttons or []))
        if not result.ok:
            await render_user_screen(bot, first.from_user.id,
                "<b>❌ Пост не прошёл проверку</b>\n\n"
                f"Причина: <b>{escape(result.error or 'неизвестная ошибка')}</b>",
                nav(order.id), media_key="revision", text_only=True)
            return
        order.content_text = html
        order.media = media
        order.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await update_buyer_card(session, bot, order)
    await state.clear()
    for item in messages:
        await delete_user_input(item)


@router.message(RevisionFlow.waiting_post)
async def receive_replacement(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.media_group_id:
        collector.add(message, lambda items: process_replacement(items, state, bot))
        return
    await process_replacement([message], state, bot)


@router.callback_query(F.data.startswith("revisionv9:submit:"))
async def resubmit(callback: CallbackQuery, bot: Bot) -> None:
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.scalar(select(AdOrder).where(
            AdOrder.id == order_id,
            AdOrder.user_id == callback.from_user.id,
        ).with_for_update())
        if not order or order.status != OrderStatus.REVISION.value:
            await callback.answer("Заявка уже отправлена или обработана.", show_alert=True)
            return
        if not order.content_text and not order.media:
            await callback.answer("Сначала добавьте текст или фотографию.", show_alert=True)
            return
        user = await session.get(User, order.user_id)
        if not user:
            await callback.answer("Профиль покупателя не найден.", show_alert=True)
            return
        old_card_id = order.moderation_card_message_id
        order.status = OrderStatus.MODERATION.value
        order.moderated_by = None
        order.moderated_at = None
        order.updated_at = datetime.now(timezone.utc)
        decision = await session.get(OrderDecision, order.id)
        now = datetime.now(timezone.utc)
        if decision is None:
            session.add(OrderDecision(order_id=order.id, action="resubmitted",
                comment="Покупатель отправил исправленную версию.",
                decided_by=order.user_id, decided_at=now))
        else:
            decision.action = "resubmitted"
            decision.comment = "Покупатель отправил исправленную версию."
            decision.decided_by = order.user_id
            decision.decided_at = now
        await session.commit()
        if old_card_id:
            await update_staff_card(session, bot, order,
                f"<b>🔄 Заявка №{order.id} отправлена повторно</b>\n\n"
                "Старая карточка закрыта. Новая версия отправлена ниже.")
        try:
            order.moderation_card_message_id = await deliver_order_to_staff(bot, order, user)
            order.updated_at = datetime.now(timezone.utc)
            await session.commit()
        except Exception as error:
            await notify_delivery_failure(bot, order.id, error)
        await update_buyer_card(session, bot, order, source_message=callback.message)
    await callback.answer("Исправленная версия отправлена", show_alert=True)