from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.config import get_settings
from app.db.session import SessionFactory
from app.enums import OrderStatus, TariffCode
from app.keyboards import test_payment_keyboard
from app.models import AdOrder
from app.services.orders import deposit_amount, slot_available
from app.services.telegram_ads import activate_order, capture_middle_pin

router = Router(name="moderation")
settings = get_settings()


def is_staff_callback(callback: CallbackQuery) -> bool:
    return bool(callback.message and callback.message.chat.id == settings.staff_chat_id)


@router.callback_query(F.data.startswith("mod:"))
async def moderation_action(callback: CallbackQuery, bot: Bot) -> None:
    if not is_staff_callback(callback):
        await callback.answer("Кнопка работает только в группе состава.", show_alert=True)
        return
    _, action, raw_id = callback.data.split(":", 2)
    order_id = int(raw_id)
    now = datetime.now(timezone.utc)

    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
        if not order:
            await callback.answer("Заказ не найден.", show_alert=True)
            return

        if action == "activate":
            if order.status != OrderStatus.READY.value:
                await callback.answer("Заказ ещё не готов.", show_alert=True)
                return
            if not await slot_available(
                session,
                order.tariff_code,
                now,
                now + timedelta(hours=order.duration_hours),
                order.id,
            ):
                await callback.answer("Нет свободного места.", show_alert=True)
                return
            await activate_order(session, bot, order, callback.from_user.id)
            await callback.message.edit_text(
                f"✅ Заказ №{order.id} активирован участником {callback.from_user.full_name}."
            )
            suffix = (
                " Отправьте первое сообщение в барахолку — бот закрепит его."
                if order.tariff_code == TariffCode.MIDDLE.value
                else ""
            )
            await bot.send_message(order.user_id, f"🚀 Реклама №{order.id} активирована.{suffix}")
            await callback.answer()
            return

        if order.status != OrderStatus.MODERATION.value:
            await callback.answer("Заявку уже обработали.", show_alert=True)
            return

        order.moderated_by = callback.from_user.id
        order.moderated_at = now
        order.updated_at = now
        if action == "approve":
            if order.requested_start_at:
                order.status = OrderStatus.AWAITING_DEPOSIT.value
                amount = deposit_amount(order.price_rub)
                keyboard = test_payment_keyboard(order.id, "deposit", amount)
                text = f"✅ Заявка №{order.id} одобрена. Тестовая предоплата: {amount} ₽."
            else:
                order.status = OrderStatus.AWAITING_PAYMENT.value
                amount = order.price_rub
                keyboard = test_payment_keyboard(order.id, "full", amount)
                text = f"✅ Заявка №{order.id} одобрена. Тестовая оплата: {amount} ₽."
            await bot.send_message(order.user_id, text, reply_markup=keyboard)
        elif action == "revision":
            order.status = OrderStatus.REVISION.value
            await bot.send_message(
                order.user_id,
                f"✏️ Заявка №{order.id} возвращена на исправление. Создайте новую заявку.",
            )
        elif action == "reject":
            order.status = OrderStatus.REJECTED.value
            await bot.send_message(order.user_id, f"❌ Заявка №{order.id} отклонена.")
        else:
            await callback.answer("Неизвестное действие.", show_alert=True)
            return
        await session.commit()

    await callback.message.edit_text(
        f"Заявка №{order_id}: {action}. Решение: {callback.from_user.full_name}."
    )
    await callback.answer()


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
            .order_by(AdOrder.activated_at.desc())
        )
        if order:
            await capture_middle_pin(session, bot, order, message)
            await bot.send_message(order.user_id, f"📌 Сообщение закреплено по заказу №{order.id}.")
