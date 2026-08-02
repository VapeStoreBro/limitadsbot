from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from app.db.session import SessionFactory
from app.enums import OrderStatus, TariffCode
from app.keyboards import DURATION_NAMES, TARIFF_NAMES
from app.models import AdOrder, OrderNotice, User
from app.services.lifecycle import auto_activate_paid_order, complete_order
from app.services.order_cards import update_buyer_card
from app.services.staff_delivery import deliver_order_to_staff
from app.services.telegram_ads import (
    publish_best_copy,
    refresh_user_prefix,
    restore_order_pin,
)
from app.services.ui_screen import render_user_screen
from app.services.users import is_admin

router = Router(name="single_actions_v6")

STATUS = {
    OrderStatus.MODERATION.value: "На модерации",
    OrderStatus.AWAITING_PAYMENT.value: "Ждёт оплату",
    OrderStatus.AWAITING_DEPOSIT.value: "Ждёт предоплату",
    OrderStatus.BOOKED.value: "В брони/очереди",
    OrderStatus.READY.value: "Оплачено — автозапуск",
    OrderStatus.ACTIVE.value: "Активно",
    OrderStatus.REVISION.value: "На исправлении",
    OrderStatus.REJECTED.value: "Отклонено",
    OrderStatus.COMPLETED.value: "Завершено",
    OrderStatus.CANCELLED.value: "Отменено",
}


async def allowed(callback: CallbackQuery) -> bool:
    if callback.message and callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer("Раздел доступен только в личке.", show_alert=True)
        return False
    async with SessionFactory() as session:
        value = await is_admin(session, callback.from_user.id)
    if not value:
        await callback.answer("Доступ запрещён.", show_alert=True)
    return value


async def screen(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    await render_user_screen(
        callback.bot,
        callback.from_user.id,
        text,
        markup,
        source_message=callback.message,
        media_key="main",
    )


def admin_nav() -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(text="⬅️ Админ-панель", callback_data="profile:admin"),
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home"),
    ]


def order_text(order: AdOrder, user: User) -> str:
    return (
        f"<b><u>📦 ЗАКАЗ №{order.id}</u></b>\n\n"
        f"<b>Статус:</b> {STATUS.get(order.status, escape(order.status))}\n\n"
        f"├ Клиент: <a href=\"tg://user?id={user.id}\">{escape(user.first_name or str(user.id))}</a>\n"
        f"├ ID: <code>{user.id}</code>\n"
        f"├ Тариф: <b>{TARIFF_NAMES.get(order.tariff_code, order.tariff_code)}</b>\n"
        f"├ Срок: <b>{DURATION_NAMES.get(order.duration_code, order.duration_code)}</b>\n"
        f"├ Цена: <b>{order.price_rub} ₽</b>\n"
        f"└ Оплачено: <b>{order.paid_rub} ₽</b>"
    )


def order_markup(order: AdOrder) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="👁 Показать пост",
                callback_data=f"adminv5:show_order:{order.id}",
                style="primary",
            )
        ]
    ]
    if order.status == OrderStatus.MODERATION.value:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📨 Повторить отправку в стаф",
                    callback_data=f"adminorder:resend:{order.id}",
                )
            ]
        )
    if order.status == OrderStatus.READY.value and order.paid_rub >= order.price_rub:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔄 Проверить автозапуск",
                    callback_data=f"adminorder:activate:{order.id}",
                    style="success",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ К заказам", callback_data="adminorder:list")])
    rows.append(admin_nav())
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.in_({"adminv3:orders", "adminorder:list"}))
async def orders_list(callback: CallbackQuery) -> None:
    if not await allowed(callback):
        return
    statuses = [
        OrderStatus.MODERATION.value,
        OrderStatus.AWAITING_PAYMENT.value,
        OrderStatus.AWAITING_DEPOSIT.value,
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
    rows = [
        [
            InlineKeyboardButton(
                text=f"№{order.id} · {order.tariff_code} · {STATUS.get(order.status, order.status)}",
                callback_data=f"adminorder:view:{order.id}",
            )
        ]
        for order in orders
    ]
    rows.append(admin_nav())
    await screen(
        callback,
        "<b><u>📥 ЗАЯВКИ И ЗАКАЗЫ</u></b>\n\n"
        + ("Выберите заказ." if orders else "Заказов, требующих действий, нет."),
        InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adminorder:view:"))
async def order_view(callback: CallbackQuery) -> None:
    if not await allowed(callback):
        return
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
        user = await session.get(User, order.user_id) if order else None
    if not order or not user:
        await callback.answer("Заказ не найден.", show_alert=True)
        return
    await screen(callback, order_text(order, user), order_markup(order))
    await callback.answer()


@router.callback_query(F.data.startswith("adminorder:resend:"))
async def resend(callback: CallbackQuery) -> None:
    if not await allowed(callback):
        return
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
        user = await session.get(User, order.user_id) if order else None
        if not order or not user:
            await callback.answer("Заказ не найден.", show_alert=True)
            return
        try:
            order.moderation_card_message_id = await deliver_order_to_staff(
                callback.bot,
                order,
                user,
            )
            order.updated_at = datetime.now(timezone.utc)
            await session.commit()
            await screen(callback, order_text(order, user), order_markup(order))
            await callback.answer("Отправлено в стаф", show_alert=True)
        except Exception as error:
            await callback.answer(f"Telegram: {error}", show_alert=True)


@router.callback_query(F.data.startswith("adminorder:activate:"))
async def stale_activate(callback: CallbackQuery) -> None:
    if not await allowed(callback):
        return
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
        user = await session.get(User, order.user_id) if order else None
        if not order or not user:
            await callback.answer("Заказ не найден.", show_alert=True)
            return
        if order.paid_rub >= order.price_rub:
            await auto_activate_paid_order(session, callback.bot, order)
        await screen(callback, order_text(order, user), order_markup(order))
    await callback.answer("Автозапуск проверен", show_alert=True)


def active_text(order: AdOrder) -> str:
    return (
        f"<b><u>🚀 АКТИВНАЯ РЕКЛАМА №{order.id}</u></b>\n\n"
        f"├ Клиент: <code>{order.user_id}</code>\n"
        f"├ Тариф: <b>{TARIFF_NAMES.get(order.tariff_code, order.tariff_code)}</b>\n"
        f"├ Начало: <code>{order.activated_at:%d.%m.%Y %H:%M}</code>\n"
        f"├ Окончание: <code>{order.ends_at:%d.%m.%Y %H:%M}</code>\n"
        f"└ Текущий закреп: <b>{'есть' if order.pinned_message_id else 'временно нет'}</b>"
    )


def active_markup(order: AdOrder) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="👁 Показать пост",
                callback_data=f"activev3:show:{order.id}",
                style="primary",
            )
        ],
        [
            InlineKeyboardButton(text="+1 день", callback_data=f"activev3:extend:{order.id}:24"),
            InlineKeyboardButton(text="+7 дней", callback_data=f"activev3:extend:{order.id}:168"),
            InlineKeyboardButton(text="+30 дней", callback_data=f"activev3:extend:{order.id}:720"),
        ],
    ]
    if order.pinned_message_id:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📌 Поднять этот пост в закрепах",
                    callback_data=f"activev3:repin:{order.id}",
                )
            ]
        )
    if order.tariff_code == TariffCode.BEST.value:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📣 Отправить и закрепить Best сейчас",
                    callback_data=f"activev3:publish:{order.id}",
                    style="success",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="🛑 Завершить размещение",
                callback_data=f"activev3:stop_confirm:{order.id}",
                style="danger",
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="⬅️ К активным", callback_data="adminv3:active")])
    rows.append(admin_nav())
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "adminv3:active")
async def active_list(callback: CallbackQuery) -> None:
    if not await allowed(callback):
        return
    async with SessionFactory() as session:
        orders = (
            await session.scalars(
                select(AdOrder)
                .where(AdOrder.status == OrderStatus.ACTIVE.value)
                .order_by(AdOrder.ends_at)
            )
        ).all()
    rows = [
        [
            InlineKeyboardButton(
                text=f"№{order.id} · {order.tariff_code} · до {order.ends_at:%d.%m %H:%M}",
                callback_data=f"activev3:view:{order.id}",
            )
        ]
        for order in orders
    ]
    rows.append(admin_nav())
    await screen(
        callback,
        "<b><u>🚀 АКТИВНАЯ РЕКЛАМА</u></b>\n\n"
        + ("Выберите размещение." if orders else "Активных размещений нет."),
        InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("activev3:view:"))
async def active_view(callback: CallbackQuery) -> None:
    if not await allowed(callback):
        return
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
    if not order or order.status != OrderStatus.ACTIVE.value:
        await callback.answer("Размещение уже не активно.", show_alert=True)
        return
    await screen(callback, active_text(order), active_markup(order))
    await callback.answer()


@router.callback_query(F.data.startswith("activev3:extend:"))
async def extend(callback: CallbackQuery) -> None:
    if not await allowed(callback):
        return
    _, _, raw_id, raw_hours = callback.data.split(":", 3)
    order_id, hours = int(raw_id), int(raw_hours)
    async with SessionFactory() as session:
        order = await session.scalar(
            select(AdOrder).where(AdOrder.id == order_id).with_for_update()
        )
        if not order or order.status != OrderStatus.ACTIVE.value or not order.ends_at:
            await callback.answer("Размещение уже не активно.", show_alert=True)
            return
        order.ends_at += timedelta(hours=hours)
        order.requested_end_at = order.ends_at
        order.duration_hours += hours
        order.updated_at = datetime.now(timezone.utc)
        warning = await session.scalar(
            select(OrderNotice).where(
                OrderNotice.order_id == order.id,
                OrderNotice.code == "ends_in_3_days",
            )
        )
        if warning:
            await session.delete(warning)
        await session.commit()
        await refresh_user_prefix(session, callback.bot, order.user_id)
        await update_buyer_card(session, callback.bot, order)
        await screen(callback, active_text(order), active_markup(order))
    await callback.answer("Продлено", show_alert=True)


@router.callback_query(F.data.startswith("activev3:repin:"))
async def repin(callback: CallbackQuery) -> None:
    if not await allowed(callback):
        return
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
        if not order or order.status != OrderStatus.ACTIVE.value:
            await callback.answer("Размещение уже не активно.", show_alert=True)
            return
        if not await restore_order_pin(session, callback.bot, order):
            await callback.answer("У заказа нет сохранённого закрепа.", show_alert=True)
            return
        await screen(callback, active_text(order), active_markup(order))
    await callback.answer("Пост поднят в закрепах", show_alert=True)


@router.callback_query(F.data.startswith("activev3:publish:"))
async def publish(callback: CallbackQuery) -> None:
    if not await allowed(callback):
        return
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
        if not order or order.status != OrderStatus.ACTIVE.value or order.tariff_code != TariffCode.BEST.value:
            await callback.answer("Доступно только активному Best.", show_alert=True)
            return
        await publish_best_copy(session, callback.bot, order)
        await screen(callback, active_text(order), active_markup(order))
    await callback.answer("Best отправлен и поднят в закрепах", show_alert=True)


@router.callback_query(F.data.startswith("activev3:stop_confirm:"))
async def stop_confirm(callback: CallbackQuery) -> None:
    if not await allowed(callback):
        return
    order_id = int(callback.data.rsplit(":", 1)[1])
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛑 Да, завершить",
                    callback_data=f"activev3:stop:{order_id}",
                    style="danger",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Нет", callback_data=f"activev3:view:{order_id}")],
        ]
    )
    await screen(
        callback,
        f"<b>Завершить размещение №{order_id}?</b>\n\n"
        "Закреп снимется, автоматизация остановится, префикс обновится.",
        markup,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("activev3:stop:"))
async def stop(callback: CallbackQuery) -> None:
    if not await allowed(callback):
        return
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.scalar(
            select(AdOrder).where(AdOrder.id == order_id).with_for_update()
        )
        if not order:
            await callback.answer("Заказ не найден.", show_alert=True)
            return
        if order.status == OrderStatus.ACTIVE.value:
            await complete_order(session, callback.bot, order, cancelled=True)
        await screen(
            callback,
            f"<b>🏁 Размещение №{order.id} завершено</b>\n\n"
            "Закреп снят, автоматизация остановлена, префикс обновлён.",
            InlineKeyboardMarkup(inline_keyboard=[admin_nav()]),
        )
    await callback.answer("Состояние обновлено", show_alert=True)
