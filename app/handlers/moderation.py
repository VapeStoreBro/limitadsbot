from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.config import get_settings
from app.db.session import SessionFactory
from app.enums import OrderStatus, TariffCode
from app.keyboards import test_payment_keyboard
from app.models import AdOrder
from app.services.app_settings import get_staff_chat_id
from app.services.orders import deposit_amount
from app.services.telegram_ads import capture_middle_pin

router = Router(name="moderation")
settings = get_settings()


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

        if action == "approve":
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
            result_text = "одобрена"
        elif action == "revision":
            order.status = OrderStatus.REVISION.value
            await session.commit()
            await bot.send_message(
                order.user_id,
                f"<b>✏️ Заявка №{order.id} возвращена на исправление</b>\n\n"
                "Откройте главное меню и оформите исправленный пост заново.",
            )
            result_text = "возвращена на исправление"
        else:
            order.status = OrderStatus.REJECTED.value
            await session.commit()
            await bot.send_message(
                order.user_id,
                f"<b>❌ Заявка №{order.id} отклонена</b>\n\n"
                "Для уточнения причины свяжитесь с администрацией.",
            )
            result_text = "отклонена"

    await callback.message.edit_text(
        f"Заявка №{order_id} {result_text}.\n"
        f"Решение: <b>{callback.from_user.full_name}</b>."
    )
    await callback.answer("Готово")


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
            await bot.send_message(
                order.user_id,
                f"<b>📌 Новый закреп установлен</b>\n\n"
                f"Заказ №{order.id}. Использовано замен: <b>{order.pin_changes_used}/2</b>.",
            )
