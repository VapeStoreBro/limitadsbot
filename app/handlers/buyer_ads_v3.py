from datetime import datetime, timezone
from html import escape
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import select, update

from app.config import get_settings
from app.db.session import SessionFactory
from app.enums import OrderStatus, TariffCode
from app.keyboards import DURATION_NAMES, TARIFF_NAMES
from app.keyboards_v3 import (
    buyer_order_actions,
    buyer_orders_keyboard,
    buyer_stop_confirmation,
    home_keyboard,
)
from app.models import AdOrder
from app.services.staff_delivery import send_ad_content_resilient
from app.services.telegram_ads import finish_order

router = Router(name="buyer_ads_v3")
settings = get_settings()

STATUS_LABELS = {
    OrderStatus.MODERATION.value: "🛡 Администрация проверяет пост",
    OrderStatus.REVISION.value: "✏️ Нужно исправить пост и отправить новую заявку",
    OrderStatus.REJECTED.value: "❌ Заявка отклонена",
    OrderStatus.AWAITING_PAYMENT.value: "💳 Заявка одобрена — нужна полная оплата",
    OrderStatus.AWAITING_DEPOSIT.value: "💳 Место одобрено — нужна предоплата 50%",
    OrderStatus.BOOKED.value: "📅 Место забронировано",
    OrderStatus.READY.value: "🚀 Полностью оплачено — администрация запускает рекламу",
    OrderStatus.ACTIVE.value: "✅ Реклама сейчас работает",
    OrderStatus.COMPLETED.value: "🏁 Оплаченный период завершён",
    OrderStatus.CANCELLED.value: "🚫 Заказ отменён",
}

NEXT_STEPS = {
    OrderStatus.MODERATION.value: "Ожидайте решение в этом боте.",
    OrderStatus.REVISION.value: "Исправьте замечания и оформите новый пост через главное меню.",
    OrderStatus.REJECTED.value: "Комментарий администрации находится в сообщении с решением.",
    OrderStatus.AWAITING_PAYMENT.value: "Нажмите кнопку оплаты в сообщении об одобрении.",
    OrderStatus.AWAITING_DEPOSIT.value: "Внесите предоплату по кнопке в сообщении об одобрении.",
    OrderStatus.BOOKED.value: "Следите за сроком доплаты в карточке бронирования.",
    OrderStatus.READY.value: "Ничего отправлять не нужно — администрация активирует рекламу.",
    OrderStatus.ACTIVE.value: "Управление активной рекламой доступно кнопками ниже.",
    OrderStatus.COMPLETED.value: "Можно оформить новое размещение из главного меню.",
    OrderStatus.CANCELLED.value: "Автоматизация остановлена, закреп и префикс сняты.",
}


def order_text(order: AdOrder) -> str:
    local_zone = ZoneInfo(settings.timezone)
    start = order.activated_at.astimezone(local_zone) if order.activated_at else None
    end = order.ends_at.astimezone(local_zone) if order.ends_at else None
    lines = [
        f"<b><u>📦 РЕКЛАМА №{order.id}</u></b>",
        "",
        f"<b>Статус:</b> {STATUS_LABELS.get(order.status, escape(order.status))}",
        f"<b>Что дальше:</b> {NEXT_STEPS.get(order.status, 'Откройте карточку позже для обновления статуса.')}",
        "",
        f"├ Тариф: <b>{TARIFF_NAMES.get(order.tariff_code, order.tariff_code)}</b>",
        f"├ Срок: <b>{DURATION_NAMES.get(order.duration_code, order.duration_code)}</b>",
        f"├ Стоимость: <b>{order.price_rub} ₽</b>",
        f"├ Оплачено: <b>{order.paid_rub} ₽</b>",
    ]
    if order.tariff_code == TariffCode.MIDDLE.value:
        if order.awaiting_middle_pin and order.pinned_message_id:
            lines.append("├ Закреп: <b>старый пост закреплён, бот ждёт новый</b>")
        elif order.awaiting_middle_pin:
            lines.append("├ Закреп: <b>бот ждёт первый пост в барахолке</b>")
        elif order.pinned_message_id:
            lines.append("├ Закреп: <b>установлен</b>")
        else:
            lines.append("├ Закреп: <b>не установлен</b>")
        lines.append(f"├ Использовано замен: <b>{order.pin_changes_used}/2</b>")
    lines.append(
        f"├ Запуск: <code>{start:%d.%m.%Y %H:%M}</code>"
        if start
        else "├ Запуск: ещё не выполнен"
    )
    lines.append(
        f"└ Окончание: <code>{end:%d.%m.%Y %H:%M}</code>"
        if end
        else "└ Окончание: ещё не определено"
    )
    return "\n".join(lines)


async def get_owned_order(user_id: int, order_id: int) -> AdOrder | None:
    async with SessionFactory() as session:
        return await session.scalar(
            select(AdOrder).where(
                AdOrder.id == order_id,
                AdOrder.user_id == user_id,
            )
        )


@router.callback_query(F.data == "profile:orders")
async def buyer_orders(callback: CallbackQuery) -> None:
    async with SessionFactory() as session:
        orders = (
            await session.scalars(
                select(AdOrder)
                .where(AdOrder.user_id == callback.from_user.id)
                .order_by(AdOrder.id.desc())
                .limit(30)
            )
        ).all()
    await callback.answer()
    if not orders:
        await callback.bot.send_message(
            callback.from_user.id,
            "<b>📂 Мои рекламы</b>\n\nУ вас пока нет оформленных реклам.",
            reply_markup=home_keyboard(),
        )
        return
    await callback.bot.send_message(
        callback.from_user.id,
        "<b><u>📂 МОИ РЕКЛАМЫ</u></b>\n\n"
        "Откройте нужную рекламу — внутри будут понятный статус и доступные действия.",
        reply_markup=buyer_orders_keyboard(orders),
    )


@router.callback_query(F.data.startswith("buyerorder:view:"))
async def buyer_order_view(callback: CallbackQuery) -> None:
    order_id = int(callback.data.rsplit(":", 1)[1])
    order = await get_owned_order(callback.from_user.id, order_id)
    if not order:
        await callback.answer("Реклама не найдена.", show_alert=True)
        return
    await callback.answer("Открываю")
    await callback.bot.send_message(
        callback.from_user.id,
        order_text(order),
        reply_markup=buyer_order_actions(order),
    )


@router.callback_query(F.data.startswith("buyerorder:show:"))
async def buyer_order_show(callback: CallbackQuery) -> None:
    order_id = int(callback.data.rsplit(":", 1)[1])
    order = await get_owned_order(callback.from_user.id, order_id)
    if not order:
        await callback.answer("Реклама не найдена.", show_alert=True)
        return
    await callback.answer("Показываю пост")
    await callback.bot.send_message(
        callback.from_user.id,
        "<b>👁 Ваш рекламный пост</b>",
    )
    await send_ad_content_resilient(callback.bot, callback.from_user.id, order)


async def pin_help_text(order: AdOrder) -> str:
    if order.pinned_message_id:
        return (
            "<b>📌 Как заменить закреп</b>\n\n"
            "1. Старый пост пока остаётся закреплённым.\n"
            "2. Отправьте новый рекламный пост <b>в саму барахолку</b>.\n"
            "3. Бот закрепит новый пост и только затем снимет старый.\n"
            f"4. После замены останется попыток: <b>{max(0, 1 - order.pin_changes_used)}</b>."
        )
    return (
        "<b>📌 Как установить первый закреп</b>\n\n"
        "Отправьте ваш первый рекламный пост <b>в саму барахолку</b>. "
        "Бот увидит следующее сообщение от вас и закрепит его автоматически."
    )


@router.callback_query(F.data.startswith("buyerorder:pin_help:"))
async def buyer_pin_help(callback: CallbackQuery) -> None:
    order_id = int(callback.data.rsplit(":", 1)[1])
    order = await get_owned_order(callback.from_user.id, order_id)
    if not order:
        await callback.answer("Реклама не найдена.", show_alert=True)
        return
    await callback.answer()
    await callback.bot.send_message(
        callback.from_user.id,
        await pin_help_text(order),
        reply_markup=buyer_order_actions(order),
    )


@router.callback_query(F.data.startswith("buyerorder:pin:"))
async def buyer_request_pin(callback: CallbackQuery) -> None:
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.scalar(
            select(AdOrder).where(
                AdOrder.id == order_id,
                AdOrder.user_id == callback.from_user.id,
            )
        )
        if not order or order.status != OrderStatus.ACTIVE.value:
            await callback.answer("Реклама не активна.", show_alert=True)
            return
        if order.tariff_code != TariffCode.MIDDLE.value:
            await callback.answer("Закреп управляется только в Middle.", show_alert=True)
            return
        if order.pinned_message_id and order.pin_changes_used >= 2:
            await callback.answer("Две замены уже использованы.", show_alert=True)
            return

        await session.execute(
            update(AdOrder)
            .where(
                AdOrder.user_id == callback.from_user.id,
                AdOrder.tariff_code == TariffCode.MIDDLE.value,
                AdOrder.status == OrderStatus.ACTIVE.value,
                AdOrder.id != order.id,
            )
            .values(awaiting_middle_pin=False)
        )
        order.awaiting_middle_pin = True
        order.updated_at = datetime.now(timezone.utc)
        await session.commit()

    await callback.answer("Бот ждёт ваш пост в барахолке", show_alert=True)
    refreshed = await get_owned_order(callback.from_user.id, order_id)
    await callback.bot.send_message(
        callback.from_user.id,
        await pin_help_text(refreshed),
        reply_markup=buyer_order_actions(refreshed),
    )


@router.callback_query(F.data.startswith("buyerorder:pin_cancel:"))
async def buyer_cancel_pin(callback: CallbackQuery) -> None:
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.scalar(
            select(AdOrder).where(
                AdOrder.id == order_id,
                AdOrder.user_id == callback.from_user.id,
            )
        )
        if not order:
            await callback.answer("Реклама не найдена.", show_alert=True)
            return
        order.awaiting_middle_pin = False
        order.updated_at = datetime.now(timezone.utc)
        await session.commit()
    await callback.answer("Ожидание поста отменено", show_alert=True)
    refreshed = await get_owned_order(callback.from_user.id, order_id)
    await callback.bot.send_message(
        callback.from_user.id,
        order_text(refreshed),
        reply_markup=buyer_order_actions(refreshed),
    )


@router.callback_query(F.data.startswith("buyerorder:stop_confirm:"))
async def buyer_stop_confirm(callback: CallbackQuery) -> None:
    order_id = int(callback.data.rsplit(":", 1)[1])
    order = await get_owned_order(callback.from_user.id, order_id)
    if not order or order.status != OrderStatus.ACTIVE.value:
        await callback.answer("Реклама не активна.", show_alert=True)
        return
    await callback.answer()
    await callback.bot.send_message(
        callback.from_user.id,
        "<b>⚠️ Завершить рекламу досрочно?</b>\n\n"
        "Автопубликации остановятся, закреп будет снят, а префикс удалится или пересчитается. "
        "Сообщения из группы не удаляются.",
        reply_markup=buyer_stop_confirmation(order.id),
    )


@router.callback_query(F.data.startswith("buyerorder:stop:"))
async def buyer_stop(callback: CallbackQuery, bot: Bot) -> None:
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.scalar(
            select(AdOrder).where(
                AdOrder.id == order_id,
                AdOrder.user_id == callback.from_user.id,
            )
        )
        if not order or order.status != OrderStatus.ACTIVE.value:
            await callback.answer("Реклама уже завершена.", show_alert=True)
            return
        await finish_order(session, bot, order)
    await callback.answer("Реклама завершена", show_alert=True)
    await callback.message.edit_text(
        f"<b>🏁 Реклама №{order_id} завершена</b>\n\n"
        "Закреп снят, автоматизация остановлена, префикс обновлён. Старые сообщения остались в группе.",
        reply_markup=home_keyboard(),
    )
