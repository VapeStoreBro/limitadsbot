from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.config import get_settings
from app.db.session import SessionFactory
from app.enums import OrderStatus, TariffCode
from app.keyboards import moderation_keyboard, test_payment_keyboard
from app.keyboards_v3 import home_keyboard, moderation_reason_keyboard
from app.models import AdOrder
from app.services.app_settings import get_staff_chat_id
from app.services.orders import deposit_amount
from app.services.telegram_ads import capture_middle_pin

router = Router(name="moderation")
settings = get_settings()

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


@router.callback_query(F.data.startswith("mod:"))
async def moderation_action(callback: CallbackQuery, bot: Bot) -> None:
    if not await is_staff_callback(callback):
        await callback.answer("Кнопка работает только в группе состава.", show_alert=True)
        return

    _, action, raw_id = callback.data.split(":", 2)
    if action not in {"approve", "revision", "reject"}:
        await callback.answer(
            "В группе состава доступны только одобрение, исправление и отклонение.",
            show_alert=True,
        )
        return

    order_id = int(raw_id)
    if action in {"revision", "reject"}:
        async with SessionFactory() as session:
            order = await session.get(AdOrder, order_id)
            if not order:
                await callback.answer("Заказ не найден.", show_alert=True)
                return
            if order.status != OrderStatus.MODERATION.value:
                await callback.answer("Заявку уже обработали.", show_alert=True)
                return
        title = "Причина возврата на исправление" if action == "revision" else "Причина отклонения"
        await callback.message.edit_text(
            f"<b>{title} · заявка №{order_id}</b>\n\n"
            "Выберите короткий комментарий — покупатель получит его вместе с решением.",
            reply_markup=moderation_reason_keyboard(action, order_id),
        )
        await callback.answer("Выберите причину")
        return

    now = datetime.now(timezone.utc)
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
        if not order:
            await callback.answer("Заказ не найден.", show_alert=True)
            return
        if order.status != OrderStatus.MODERATION.value:
            await callback.answer("Заявку уже обработали.", show_alert=True)
            return

        order.moderated_by = callback.from_user.id
        order.moderated_at = now
        order.updated_at = now
        if order.requested_start_at:
            order.status = OrderStatus.AWAITING_DEPOSIT.value
            amount = deposit_amount(order.price_rub)
            kind = "deposit"
            text = (
                f"<b>✅ Заявка №{order.id} одобрена</b>\n\n"
                f"Для бронирования внесите тестовую предоплату <b>{amount} ₽</b>."
            )
        else:
            order.status = OrderStatus.AWAITING_PAYMENT.value
            amount = order.price_rub
            kind = "full"
            text = (
                f"<b>✅ Заявка №{order.id} одобрена</b>\n\n"
                f"Для продолжения внесите тестовую оплату <b>{amount} ₽</b>."
            )
        await session.commit()
        await bot.send_message(
            order.user_id,
            text,
            reply_markup=test_payment_keyboard(order.id, kind, amount),
        )

    await callback.message.edit_text(
        f"✅ Заявка №{order_id} одобрена.\n"
        f"Решение: <b>{callback.from_user.full_name}</b>."
    )
    await callback.answer("Готово")


@router.callback_query(F.data.startswith("modreason:"))
async def moderation_reason(callback: CallbackQuery, bot: Bot) -> None:
    if not await is_staff_callback(callback):
        await callback.answer("Кнопка работает только в группе состава.", show_alert=True)
        return
    _, action, raw_id, reason_code = callback.data.split(":", 3)
    order_id = int(raw_id)

    if action == "back":
        await callback.message.edit_text(
            f"<b>Решение по заявке №{order_id}</b>\n"
            "Проверьте пост и выберите действие.",
            reply_markup=moderation_keyboard(order_id),
        )
        await callback.answer()
        return

    if action not in {"revision", "reject"}:
        await callback.answer("Неизвестное решение.", show_alert=True)
        return

    now = datetime.now(timezone.utc)
    reason = REASON_TEXTS.get(reason_code, REASON_TEXTS["none"])
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
        if not order:
            await callback.answer("Заказ не найден.", show_alert=True)
            return
        if order.status != OrderStatus.MODERATION.value:
            await callback.answer("Заявку уже обработали.", show_alert=True)
            return
        order.status = (
            OrderStatus.REVISION.value if action == "revision" else OrderStatus.REJECTED.value
        )
        order.moderated_by = callback.from_user.id
        order.moderated_at = now
        order.updated_at = now
        await session.commit()

    if action == "revision":
        buyer_text = (
            f"<b>✏️ Заявка №{order_id} возвращена на исправление</b>\n\n"
            f"Комментарий администрации: <b>{reason}</b>\n\n"
            "Исправьте материал и оформите заявку заново."
        )
        result = "возвращена на исправление"
    else:
        buyer_text = (
            f"<b>❌ Заявка №{order_id} отклонена</b>\n\n"
            f"Комментарий администрации: <b>{reason}</b>"
        )
        result = "отклонена"

    await bot.send_message(order.user_id, buyer_text, reply_markup=home_keyboard())
    await callback.message.edit_text(
        f"Заявка №{order_id} {result}.\n"
        f"Комментарий: <b>{reason}</b>\n"
        f"Решение: <b>{callback.from_user.full_name}</b>."
    )
    await callback.answer("Решение отправлено покупателю")


@router.message(F.chat.id == settings.bazaar_chat_id)
async def bazaar_messages(message: Message, bot: Bot) -> None:
    if not message.from_user:
        return
    async with SessionFactory() as session:
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
        if order:
            await capture_middle_pin(session, bot, order, message)
            remaining = max(0, 2 - order.pin_changes_used)
            await bot.send_message(
                order.user_id,
                f"<b>📌 Закреп установлен</b>\n\n"
                f"Заказ №{order.id}. Осталось замен: <b>{remaining}</b>.",
                reply_markup=home_keyboard(),
            )
