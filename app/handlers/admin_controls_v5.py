from datetime import datetime, timedelta, timezone
from html import escape

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from app.config import get_settings
from app.db.session import SessionFactory
from app.enums import OrderStatus, TariffCode
from app.keyboards import DURATION_NAMES, TARIFF_NAMES
from app.models import AdOrder, OrderNotice, User
from app.services.lifecycle import auto_activate_paid_order, complete_order
from app.services.order_cards import update_buyer_card
from app.services.staff_delivery import deliver_order_to_staff, send_ad_content_resilient
from app.services.telegram_ads import publish_best_copy, refresh_user_prefix
from app.services.users import is_admin

router = Router(name="admin_controls_v5")
settings = get_settings()

STATUS = {
    OrderStatus.MODERATION.value: "На модерации в группе состава",
    OrderStatus.AWAITING_PAYMENT.value: "Одобрено — ждёт оплату",
    OrderStatus.AWAITING_DEPOSIT.value: "Одобрено — ждёт предоплату",
    OrderStatus.BOOKED.value: "Забронировано",
    OrderStatus.READY.value: "Оплачено — автоматический запуск",
    OrderStatus.ACTIVE.value: "Активно",
    OrderStatus.REVISION.value: "Возвращено на исправление",
    OrderStatus.REJECTED.value: "Отклонено",
    OrderStatus.COMPLETED.value: "Завершено",
    OrderStatus.CANCELLED.value: "Отменено",
}


async def allowed(callback: CallbackQuery) -> bool:
    if callback.message and callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer("Управление доступно только в личке бота.", show_alert=True)
        return False
    async with SessionFactory() as session:
        result = await is_admin(session, callback.from_user.id)
    if not result:
        await callback.answer("Доступ запрещён.", show_alert=True)
    return result


async def edit(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except Exception:
        try:
            await callback.message.edit_caption(caption=text, reply_markup=markup)
        except Exception:
            await callback.bot.send_message(callback.from_user.id, text, reply_markup=markup)


def admin_nav() -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(text="⬅️ Админ-панель", callback_data="profile:admin"),
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home"),
    ]


def order_card(order: AdOrder, user: User) -> str:
    return (
        f"<b><u>📦 ЗАКАЗ №{order.id}</u></b>\n\n"
        f"<b>Статус:</b> {STATUS.get(order.status, escape(order.status))}\n\n"
        f"├ Клиент: <a href=\"tg://user?id={user.id}\">{escape(user.first_name or str(user.id))}</a>\n"
        f"├ ID: <code>{user.id}</code>\n"
        f"├ Username: @{escape(user.username) if user.username else 'нет'}\n"
        f"├ Телефон: <code>{escape(user.phone or 'не указан')}</code>\n"
        f"├ Тариф: <b>{TARIFF_NAMES.get(order.tariff_code, order.tariff_code)}</b>\n"
        f"├ Срок: <b>{DURATION_NAMES.get(order.duration_code, order.duration_code)}</b>\n"
        f"├ Цена: <b>{order.price_rub} ₽</b>\n"
        f"└ Оплачено: <b>{order.paid_rub} ₽</b>\n\n"
        "<i>Модерация выполняется в группе состава. После полной оплаты запуск автоматический.</i>"
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
                    text="🔄 Проверить автоматический запуск",
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
    await edit(
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
    await edit(callback, order_card(order, user), order_markup(order))
    await callback.answer("Открыто")


@router.callback_query(F.data.startswith("adminv5:show_order:"))
async def show_order(callback: CallbackQuery) -> None:
    if not await allowed(callback):
        return
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return
    await callback.answer("Показываю пост")
    await send_ad_content_resilient(callback.bot, callback.from_user.id, order)


@router.callback_query(F.data.startswith("adminorder:resend:"))
async def resend_staff(callback: CallbackQuery) -> None:
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
            await edit(callback, order_card(order, user), order_markup(order))
            await callback.answer("✅ Отправлено в стаф", show_alert=True)
        except Exception as error:
            await callback.answer(f"Telegram: {error}", show_alert=True)


@router.callback_query(F.data.startswith("adminorder:activate:"))
async def stale_activation_button(callback: CallbackQuery) -> None:
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
        await edit(callback, order_card(order, user), order_markup(order))
    await callback.answer("Ручная активация больше не нужна — состояние проверено", show_alert=True)


@router.callback_query(F.data.startswith(("adminorder:approve:", "adminorder:revision:", "adminorder:reject:")))
async def stale_private_moderation(callback: CallbackQuery) -> None:
    await callback.answer("Решение по заявке принимается в группе состава.", show_alert=True)


def active_card(order: AdOrder) -> str:
    return (
        f"<b><u>🚀 АКТИВНАЯ РЕКЛАМА №{order.id}</u></b>\n\n"
        f"├ Клиент: <code>{order.user_id}</code>\n"
        f"├ Тариф: <b>{TARIFF_NAMES.get(order.tariff_code, order.tariff_code)}</b>\n"
        f"├ Начало: <code>{order.activated_at:%d.%m.%Y %H:%M}</code>\n"
        f"├ Окончание: <code>{order.ends_at:%d.%m.%Y %H:%M}</code>\n"
        f"└ Основной закреп: <b>{'есть' if order.pinned_message_id else 'нет'}</b>"
    )


def active_markup(order: AdOrder) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="👁 Показать основной пост",
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
                    text="📌 Восстановить основной закреп",
                    callback_data=f"activev3:repin:{order.id}",
                )
            ]
        )
    if order.tariff_code == TariffCode.BEST.value:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📣 Отправить копию Best вне очереди",
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
    await edit(
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
    await edit(callback, active_card(order), active_markup(order))
    await callback.answer()


@router.callback_query(F.data.startswith("activev3:show:"))
async def active_show(callback: CallbackQuery) -> None:
    if not await allowed(callback):
        return
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return
    await callback.answer("Показываю пост")
    await send_ad_content_resilient(callback.bot, callback.from_user.id, order)


@router.callback_query(F.data.startswith("activev3:extend:"))
async def active_extend(callback: CallbackQuery) -> None:
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
        await edit(callback, active_card(order), active_markup(order))
    await callback.answer("Продлено", show_alert=True)


@router.callback_query(F.data.startswith("activev3:repin:"))
async def active_repin(callback: CallbackQuery) -> None:
    if not await allowed(callback):
        return
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
    if not order or order.status != OrderStatus.ACTIVE.value or not order.pinned_message_id:
        await callback.answer("Сохранённого основного закрепа нет.", show_alert=True)
        return
    try:
        await callback.bot.pin_chat_message(
            settings.bazaar_chat_id,
            order.pinned_message_id,
            disable_notification=True,
        )
        await callback.answer("Основной закреп восстановлен", show_alert=True)
    except Exception as error:
        await callback.answer(f"Telegram: {error}", show_alert=True)


@router.callback_query(F.data.startswith("activev3:publish:"))
async def active_publish(callback: CallbackQuery) -> None:
    if not await allowed(callback):
        return
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
        if not order or order.status != OrderStatus.ACTIVE.value or order.tariff_code != TariffCode.BEST.value:
            await callback.answer("Внеочередная копия доступна только активному Best.", show_alert=True)
            return
        await publish_best_copy(session, callback.bot, order)
        await edit(callback, active_card(order), active_markup(order))
    await callback.answer("Копия Best отправлена вне очереди", show_alert=True)


@router.callback_query(F.data.startswith("activev3:stop_confirm:"))
async def active_stop_confirm(callback: CallbackQuery) -> None:
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
    await edit(
        callback,
        f"<b>Завершить размещение №{order_id}?</b>\n\n"
        "Закреп снимется, автоматизация остановится, префикс обновится.",
        markup,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("activev3:stop:"))
async def active_stop(callback: CallbackQuery) -> None:
    if not await allowed(callback):
        return
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.scalar(
            select(AdOrder).where(AdOrder.id == order_id).with_for_update()
        )
        if order and order.status == OrderStatus.ACTIVE.value:
            await complete_order(session, callback.bot, order, cancelled=True)
        if order:
            await edit(
                callback,
                f"<b>🏁 Размещение №{order.id} завершено</b>\n\n"
                "Закреп снят, автоматизация остановлена, префикс обновлён.",
                InlineKeyboardMarkup(inline_keyboard=[admin_nav()]),
            )
    await callback.answer("Состояние обновлено", show_alert=True)
