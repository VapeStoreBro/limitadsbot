from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from app.config import get_settings
from app.db.session import SessionFactory
from app.enums import OrderStatus, TariffCode
from app.keyboards import DURATION_NAMES, TARIFF_NAMES, test_payment_keyboard
from app.models import AdOrder, User
from app.services.orders import deposit_amount, slot_available
from app.services.staff_delivery import (
    deliver_order_to_staff,
    notify_delivery_failure,
    send_ad_content_resilient,
)
from app.services.telegram_ads import activate_order
from app.services.users import is_admin

router = Router(name="order_admin_v2")
settings = get_settings()

STATUS_NAMES = {
    OrderStatus.MODERATION.value: "🛡 На модерации",
    OrderStatus.AWAITING_PAYMENT.value: "💳 Ожидает оплату",
    OrderStatus.AWAITING_DEPOSIT.value: "💳 Ожидает предоплату",
    OrderStatus.BOOKED.value: "📅 Забронирована",
    OrderStatus.READY.value: "🚀 Готова к запуску",
    OrderStatus.ACTIVE.value: "✅ Активна",
    OrderStatus.REVISION.value: "✏️ На исправлении",
    OrderStatus.REJECTED.value: "❌ Отклонена",
    OrderStatus.COMPLETED.value: "🏁 Завершена",
}


async def admin_allowed(user_id: int) -> bool:
    async with SessionFactory() as session:
        return await is_admin(session, user_id)


def order_card_text(order: AdOrder, user: User) -> str:
    full_name = " ".join(
        part for part in (user.first_name, user.last_name) if part
    ).strip() or str(user.id)
    username = f"@{escape(user.username)}" if user.username else "не указан"
    phone = escape(user.phone or "не указан")
    return (
        f"<b><u>📦 ЗАКАЗ №{order.id}</u></b>\n\n"
        f"<b>Статус:</b> {STATUS_NAMES.get(order.status, escape(order.status))}\n\n"
        f"<b>Покупатель</b>\n"
        f"├ Имя: <a href=\"tg://user?id={user.id}\">{escape(full_name)}</a>\n"
        f"├ ID: <code>{user.id}</code>\n"
        f"├ Username: {username}\n"
        f"└ Телефон: <code>{phone}</code>\n\n"
        f"<b>Размещение</b>\n"
        f"├ Тариф: <b>{TARIFF_NAMES.get(order.tariff_code, order.tariff_code)}</b>\n"
        f"├ Период: <b>{DURATION_NAMES.get(order.duration_code, order.duration_code)}</b>\n"
        f"├ Цена: <b>{order.price_rub} ₽</b>\n"
        f"├ Оплачено: <b>{order.paid_rub} ₽</b>\n"
        f"├ Бронь: <code>{order.requested_start_at or 'нет'}</code>\n"
        f"└ Создан: <code>{order.created_at:%d.%m.%Y %H:%M}</code>"
    )


def order_actions(order: AdOrder) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if order.status == OrderStatus.MODERATION.value:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ Одобрить",
                    callback_data=f"adminorder:approve:{order.id}",
                    style="success",
                ),
                InlineKeyboardButton(
                    text="✏️ Исправить",
                    callback_data=f"adminorder:revision:{order.id}",
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"adminorder:reject:{order.id}",
                    style="danger",
                )
            ]
        )
    if order.status == OrderStatus.READY.value:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🚀 Активировать",
                    callback_data=f"adminorder:activate:{order.id}",
                    style="success",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="📨 Повторить отправку в стаф",
                callback_data=f"adminorder:resend:{order.id}",
                style="primary",
            )
        ]
    )
    rows.append(
        [InlineKeyboardButton(text="⬅️ К списку", callback_data="adminorder:list")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_order_list(bot: Bot, chat_id: int) -> None:
    statuses = [
        OrderStatus.MODERATION.value,
        OrderStatus.AWAITING_DEPOSIT.value,
        OrderStatus.AWAITING_PAYMENT.value,
        OrderStatus.BOOKED.value,
        OrderStatus.READY.value,
    ]
    async with SessionFactory() as session:
        orders = (
            await session.scalars(
                select(AdOrder)
                .where(AdOrder.status.in_(statuses))
                .order_by(AdOrder.id.desc())
                .limit(30)
            )
        ).all()

    if not orders:
        await bot.send_message(
            chat_id,
            "<b>📥 Заявки</b>\n\n✅ Сейчас нет заявок, требующих действий.",
        )
        return

    rows = [
        [
            InlineKeyboardButton(
                text=(
                    f"№{order.id} · {TARIFF_NAMES.get(order.tariff_code, order.tariff_code)} · "
                    f"{STATUS_NAMES.get(order.status, order.status)}"
                ),
                callback_data=f"adminorder:view:{order.id}",
            )
        ]
        for order in orders
    ]
    await bot.send_message(
        chat_id,
        (
            "<b><u>📥 ЗАЯВКИ И ЗАКАЗЫ</u></b>\n\n"
            "Нажмите на заказ, чтобы увидеть пост, данные покупателя и доступные действия."
        ),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.message(F.text == "📥 Заявки")
async def admin_orders_menu(message: Message, state: FSMContext) -> None:
    if not message.from_user or not await admin_allowed(message.from_user.id):
        return
    await state.clear()
    await send_order_list(message.bot, message.chat.id)


@router.callback_query(F.data == "adminorder:list")
async def admin_orders_list_callback(callback: CallbackQuery) -> None:
    if not await admin_allowed(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await callback.answer()
    await send_order_list(callback.bot, callback.from_user.id)


@router.callback_query(F.data.startswith("adminorder:view:"))
async def admin_order_view(callback: CallbackQuery) -> None:
    if not await admin_allowed(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
        user = await session.get(User, order.user_id) if order else None
    if not order or not user:
        await callback.answer("Заказ не найден.", show_alert=True)
        return

    await callback.answer("Открываю заказ")
    await callback.bot.send_message(
        callback.from_user.id,
        order_card_text(order, user),
    )
    await callback.bot.send_message(
        callback.from_user.id,
        "<b>👁 Рекламный пост покупателя</b>",
    )
    await send_ad_content_resilient(callback.bot, callback.from_user.id, order)
    await callback.bot.send_message(
        callback.from_user.id,
        f"<b>Действия с заказом №{order.id}</b>",
        reply_markup=order_actions(order),
    )


async def load_order(order_id: int) -> tuple[AdOrder | None, User | None]:
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
        user = await session.get(User, order.user_id) if order else None
        return order, user


@router.callback_query(F.data.startswith("adminorder:resend:"))
async def admin_order_resend(callback: CallbackQuery) -> None:
    if not await admin_allowed(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    order_id = int(callback.data.rsplit(":", 1)[1])
    order, user = await load_order(order_id)
    if not order or not user:
        await callback.answer("Заказ не найден.", show_alert=True)
        return
    await callback.answer("Отправляю в группу стафа…")
    try:
        card_id = await deliver_order_to_staff(callback.bot, order, user)
        async with SessionFactory() as session:
            stored = await session.get(AdOrder, order.id)
            if stored:
                stored.moderation_card_message_id = card_id
                stored.updated_at = datetime.now(timezone.utc)
                await session.commit()
        await callback.message.answer("✅ Заявка отправлена в группу стафа.")
    except Exception as error:
        await notify_delivery_failure(callback.bot, order.id, error)
        await callback.message.answer(
            "<b>❌ Группа стафа не приняла заявку</b>\n\n"
            f"Ошибка: <code>{escape(type(error).__name__)}: {escape(str(error))}</code>"
        )


@router.callback_query(F.data.startswith("adminorder:approve:"))
async def admin_order_approve(callback: CallbackQuery) -> None:
    if not await admin_allowed(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    order_id = int(callback.data.rsplit(":", 1)[1])
    now = datetime.now(timezone.utc)
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
        if not order or order.status != OrderStatus.MODERATION.value:
            await callback.answer("Заказ уже обработан.", show_alert=True)
            return
        order.moderated_by = callback.from_user.id
        order.moderated_at = now
        order.updated_at = now
        if order.requested_start_at:
            order.status = OrderStatus.AWAITING_DEPOSIT.value
            amount = deposit_amount(order.price_rub)
            kind = "deposit"
            payment_text = (
                f"<b>✅ Заявка №{order.id} одобрена</b>\n\n"
                f"Для бронирования внесите тестовую предоплату <b>{amount} ₽</b>."
            )
        else:
            order.status = OrderStatus.AWAITING_PAYMENT.value
            amount = order.price_rub
            kind = "full"
            payment_text = (
                f"<b>✅ Заявка №{order.id} одобрена</b>\n\n"
                f"Для продолжения внесите тестовую оплату <b>{amount} ₽</b>."
            )
        await session.commit()

    await callback.bot.send_message(
        order.user_id,
        payment_text,
        reply_markup=test_payment_keyboard(order.id, kind, amount),
    )
    await callback.message.edit_text(
        f"✅ Заказ №{order.id} одобрен администратором {escape(callback.from_user.full_name)}."
    )
    await callback.answer("Одобрено")


@router.callback_query(F.data.startswith("adminorder:revision:"))
async def admin_order_revision(callback: CallbackQuery) -> None:
    await change_order_status(callback, OrderStatus.REVISION.value)


@router.callback_query(F.data.startswith("adminorder:reject:"))
async def admin_order_reject(callback: CallbackQuery) -> None:
    await change_order_status(callback, OrderStatus.REJECTED.value)


async def change_order_status(callback: CallbackQuery, status: str) -> None:
    if not await admin_allowed(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    order_id = int(callback.data.rsplit(":", 1)[1])
    now = datetime.now(timezone.utc)
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
        if not order or order.status != OrderStatus.MODERATION.value:
            await callback.answer("Заказ уже обработан.", show_alert=True)
            return
        order.status = status
        order.moderated_by = callback.from_user.id
        order.moderated_at = now
        order.updated_at = now
        await session.commit()

    if status == OrderStatus.REVISION.value:
        user_text = (
            f"<b>✏️ Заявка №{order.id} требует исправления</b>\n\n"
            "Откройте главное меню и оформите исправленный пост заново."
        )
        result_text = "возвращён на исправление"
    else:
        user_text = (
            f"<b>❌ Заявка №{order.id} отклонена</b>\n\n"
            "Для уточнения причины свяжитесь с администрацией."
        )
        result_text = "отклонён"

    await callback.bot.send_message(order.user_id, user_text)
    await callback.message.edit_text(
        f"Заказ №{order.id} {result_text}. Администратор: {escape(callback.from_user.full_name)}."
    )
    await callback.answer("Готово")


@router.callback_query(F.data.startswith("adminorder:activate:"))
async def admin_order_activate(callback: CallbackQuery) -> None:
    if not await admin_allowed(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    order_id = int(callback.data.rsplit(":", 1)[1])
    now = datetime.now(timezone.utc)
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
        if not order or order.status != OrderStatus.READY.value:
            await callback.answer("Заказ ещё не готов к запуску.", show_alert=True)
            return
        available = await slot_available(
            session,
            order.tariff_code,
            now,
            now + timedelta(hours=order.duration_hours),
            order.id,
        )
        if not available:
            await callback.answer("Для этого тарифа нет свободного места.", show_alert=True)
            return
        await activate_order(session, callback.bot, order, callback.from_user.id)

    suffix = (
        " Отправьте первый рекламный пост в барахолку — бот закрепит его."
        if order.tariff_code == TariffCode.MIDDLE.value
        else ""
    )
    await callback.bot.send_message(
        order.user_id,
        f"<b>🚀 Реклама №{order.id} активирована</b>\n\n"
        f"Префикс выдан на весь оплаченный период.{suffix}",
    )
    await callback.message.edit_text(
        f"🚀 Заказ №{order.id} активирован администратором {escape(callback.from_user.full_name)}."
    )
    await callback.answer("Реклама запущена")
