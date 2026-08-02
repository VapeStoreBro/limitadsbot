from __future__ import annotations

import re
from html import escape

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, select

from app.config import get_settings
from app.db.session import SessionFactory
from app.enums import OrderStatus
from app.keyboards import DURATION_NAMES, TARIFF_NAMES, admin_management_keyboard
from app.keyboards_v3 import (
    admin_panel_keyboard,
    client_actions_keyboard,
    settings_keyboard,
)
from app.models import AdOrder, Admin, Payment, TariffPrice, User, UserBlock, UserPrice
from app.services.ui_screen import render_user_screen
from app.services.users import is_admin

router = Router(name="single_screen_v6")
settings = get_settings()


async def admin_allowed(callback: CallbackQuery) -> bool:
    if callback.message and callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer("Раздел доступен только в личке бота.", show_alert=True)
        return False
    async with SessionFactory() as session:
        allowed = await is_admin(session, callback.from_user.id)
    if not allowed:
        await callback.answer("Доступ запрещён.", show_alert=True)
    return allowed


async def screen(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    await render_user_screen(
        callback.bot,
        callback.from_user.id,
        text,
        markup,
        source_message=callback.message,
        media_key="main",
    )


def admin_nav(rows: list[list[InlineKeyboardButton]] | None = None) -> InlineKeyboardMarkup:
    result = list(rows or [])
    result.append(
        [
            InlineKeyboardButton(text="⬅️ Админ-панель", callback_data="profile:admin"),
            InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=result)


@router.callback_query(F.data == "profile:admin")
async def admin_home(callback: CallbackQuery) -> None:
    if not await admin_allowed(callback):
        return
    await screen(
        callback,
        "<b><u>🔐 АДМИН-ПАНЕЛЬ</u></b>\n\n"
        "Все разделы открываются в этом сообщении. Группа состава используется только для решений по модерации.",
        admin_panel_keyboard(),
    )
    await callback.answer("Админ-панель")


@router.callback_query(F.data == "adminv3:bookings")
async def bookings(callback: CallbackQuery) -> None:
    if not await admin_allowed(callback):
        return
    async with SessionFactory() as session:
        orders = (
            await session.scalars(
                select(AdOrder)
                .where(AdOrder.status == OrderStatus.BOOKED.value)
                .order_by(AdOrder.requested_start_at, AdOrder.created_at)
                .limit(30)
            )
        ).all()
    rows = [
        [
            InlineKeyboardButton(
                text=f"№{order.id} · {order.tariff_code} · {order.paid_rub}/{order.price_rub} ₽",
                callback_data=f"adminorder:view:{order.id}",
            )
        ]
        for order in orders
    ]
    await screen(
        callback,
        "<b><u>📅 БРОНИРОВАНИЯ И ОЧЕРЕДЬ</u></b>\n\n"
        + ("Первый заказ получит освободившееся место автоматически." if orders else "Очередь пуста."),
        admin_nav(rows),
    )
    await callback.answer()


@router.callback_query(F.data == "adminv3:clients")
async def clients(callback: CallbackQuery) -> None:
    if not await admin_allowed(callback):
        return
    async with SessionFactory() as session:
        users = (
            await session.scalars(select(User).order_by(User.last_seen_at.desc()).limit(30))
        ).all()
    rows = [
        [
            InlineKeyboardButton(
                text=f"{user.first_name or user.id} · @{user.username or 'нет'}",
                callback_data=f"adminv3:client:{user.id}",
            )
        ]
        for user in users
    ]
    await screen(
        callback,
        "<b><u>👥 КЛИЕНТЫ</u></b>\n\nВыберите клиента.",
        admin_nav(rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adminv3:client:"))
async def client_card(callback: CallbackQuery) -> None:
    if not await admin_allowed(callback):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        user = await session.get(User, user_id)
        block = await session.get(UserBlock, user_id)
        orders_count = await session.scalar(
            select(func.count()).select_from(AdOrder).where(AdOrder.user_id == user_id)
        )
        paid = await session.scalar(
            select(func.coalesce(func.sum(AdOrder.paid_rub), 0)).where(AdOrder.user_id == user_id)
        )
        active = (
            await session.scalars(
                select(AdOrder).where(
                    AdOrder.user_id == user_id,
                    AdOrder.status == OrderStatus.ACTIVE.value,
                )
            )
        ).all()
    if not user:
        await callback.answer("Клиент не найден.", show_alert=True)
        return
    active_text = ", ".join(f"№{order.id} {order.tariff_code}" for order in active) or "нет"
    await screen(
        callback,
        "<b><u>👤 КЛИЕНТ</u></b>\n\n"
        f"├ Имя: <a href=\"tg://user?id={user.id}\"><b>{escape(user.first_name or str(user.id))}</b></a>\n"
        f"├ ID: <code>{user.id}</code>\n"
        f"├ Username: @{escape(user.username) if user.username else 'нет'}\n"
        f"├ Телефон: <code>{escape(user.phone or 'не указан')}</code>\n"
        f"├ Заказов: <b>{orders_count or 0}</b>\n"
        f"├ Оплачено: <b>{int(paid or 0)} ₽</b>\n"
        f"├ Активные: <b>{escape(active_text)}</b>\n"
        f"└ Доступ: <b>{'🚫 заблокирован' if block else '✅ разрешён'}</b>",
        client_actions_keyboard(user.id, blocked=bool(block)),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("clientv4:orders:"))
async def client_orders(callback: CallbackQuery) -> None:
    if not await admin_allowed(callback):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        orders = (
            await session.scalars(
                select(AdOrder)
                .where(AdOrder.user_id == user_id)
                .order_by(AdOrder.id.desc())
                .limit(30)
            )
        ).all()
    rows = [
        [
            InlineKeyboardButton(
                text=f"№{order.id} · {order.tariff_code} · {order.status}",
                callback_data=f"adminorder:view:{order.id}",
            )
        ]
        for order in orders
    ]
    rows.append([InlineKeyboardButton(text="⬅️ К клиенту", callback_data=f"adminv3:client:{user_id}")])
    await screen(
        callback,
        f"<b>📦 Заказы клиента <code>{user_id}</code></b>\n\n"
        + ("Выберите заказ." if orders else "Заказов нет."),
        admin_nav(rows),
    )
    await callback.answer()


@router.callback_query(F.data == "adminv3:payments")
async def payments(callback: CallbackQuery) -> None:
    if not await admin_allowed(callback):
        return
    async with SessionFactory() as session:
        items = (
            await session.scalars(select(Payment).order_by(Payment.id.desc()).limit(12))
        ).all()
    lines = ["<b><u>💳 ПОСЛЕДНИЕ ПЛАТЕЖИ</u></b>"]
    lines.extend(
        f"№{item.id} · заказ №{item.order_id} · <b>{item.amount_rub} ₽</b> · {escape(item.status)}"
        for item in items
    )
    if not items:
        lines.append("Платежей пока нет.")
    await screen(callback, "\n\n".join(lines), admin_nav())
    await callback.answer()


@router.callback_query(F.data == "adminv3:tariffs")
async def tariffs(callback: CallbackQuery) -> None:
    if not await admin_allowed(callback):
        return
    async with SessionFactory() as session:
        items = (await session.scalars(select(TariffPrice).order_by(TariffPrice.id))).all()
    lines = ["<b><u>🏷 ТАРИФЫ</u></b>"]
    lines.extend(
        f"{TARIFF_NAMES.get(item.tariff_code, item.tariff_code)} · "
        f"{DURATION_NAMES.get(item.duration_code, item.duration_code)} — <b>{item.price_rub} ₽</b>"
        for item in items
    )
    await screen(callback, "\n".join(lines), admin_nav())
    await callback.answer()


@router.callback_query(F.data == "adminv3:admins")
async def admins(callback: CallbackQuery) -> None:
    if not await admin_allowed(callback):
        return
    async with SessionFactory() as session:
        items = (await session.scalars(select(Admin).order_by(Admin.added_at))).all()
    text = "<b><u>👮 АДМИНИСТРАТОРЫ</u></b>\n\n" + "\n".join(
        f"<code>{item.user_id}</code> · {escape(item.role)}" for item in items
    )
    markup = admin_management_keyboard() if callback.from_user.id == settings.owner_id else admin_nav()
    await screen(callback, text, markup)
    await callback.answer()


@router.callback_query(F.data == "adminv3:stats")
async def stats(callback: CallbackQuery) -> None:
    if not await admin_allowed(callback):
        return
    async with SessionFactory() as session:
        users = await session.scalar(select(func.count()).select_from(User))
        orders = await session.scalar(select(func.count()).select_from(AdOrder))
        active = await session.scalar(
            select(func.count()).select_from(AdOrder).where(AdOrder.status == OrderStatus.ACTIVE.value)
        )
        booked = await session.scalar(
            select(func.count()).select_from(AdOrder).where(AdOrder.status == OrderStatus.BOOKED.value)
        )
        paid = await session.scalar(select(func.coalesce(func.sum(AdOrder.paid_rub), 0)))
    await screen(
        callback,
        "<b><u>📊 СТАТИСТИКА</u></b>\n\n"
        f"├ Пользователей: <b>{users or 0}</b>\n"
        f"├ Заказов: <b>{orders or 0}</b>\n"
        f"├ Активных: <b>{active or 0}</b>\n"
        f"├ В очереди: <b>{booked or 0}</b>\n"
        f"└ Оплачено: <b>{int(paid or 0)} ₽</b>",
        admin_nav(),
    )
    await callback.answer()


@router.callback_query(F.data == "adminv3:prices")
async def prices(callback: CallbackQuery) -> None:
    if not await admin_allowed(callback):
        return
    async with SessionFactory() as session:
        items = (
            await session.scalars(select(UserPrice).order_by(UserPrice.updated_at.desc()).limit(12))
        ).all()
    lines = ["<b><u>💰 ПЕРСОНАЛЬНЫЕ ЦЕНЫ</u></b>"]
    lines.extend(
        f"<code>{item.user_id}</code> · {item.tariff_code}/{item.duration_code} — <b>{item.price_rub} ₽</b>"
        for item in items
    )
    if not items:
        lines.append("Персональных цен пока нет.")
    rows = [[InlineKeyboardButton(text="➕ Назначить цену", callback_data="pricev3:add", style="success")]]
    await screen(callback, "\n".join(lines), admin_nav(rows))
    await callback.answer()


@router.callback_query(F.data == "adminv3:settings")
async def settings(callback: CallbackQuery) -> None:
    if not await admin_allowed(callback):
        return
    await screen(
        callback,
        "<b><u>⚙️ НАСТРОЙКИ</u></b>\n\nПроверка и изменение группы состава.",
        settings_keyboard(),
    )
    await callback.answer()


def _plain(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value or "").strip()


def preview_markup(scope: str, order: AdOrder, index: int, back_callback: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    total = len(order.media or [])
    if total > 1:
        previous = (index - 1) % total
        following = (index + 1) % total
        rows.append(
            [
                InlineKeyboardButton(text="⬅️ Фото", callback_data=f"postpreview:{scope}:{order.id}:{previous}"),
                InlineKeyboardButton(text=f"{index + 1}/{total}", callback_data="noop"),
                InlineKeyboardButton(text="Фото ➡️", callback_data=f"postpreview:{scope}:{order.id}:{following}"),
            ]
        )
    for button in order.buttons or []:
        if button.get("url"):
            rows.append([InlineKeyboardButton(text=button.get("text", "Перейти"), url=button["url"])])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_post_preview(callback: CallbackQuery, scope: str, order: AdOrder, index: int) -> None:
    media = list(order.media or [])
    index = max(0, min(index, len(media) - 1)) if media else 0
    back = f"buyerorder:view:{order.id}" if scope == "buyer" else f"adminorder:view:{order.id}"
    text = _plain(order.content_text)
    text = text[:700] + ("…" if len(text) > 700 else "")
    caption = (
        f"<b><u>👁 ПОСТ №{order.id}</u></b>\n\n"
        f"{escape(text or '(без текста)')}\n\n"
        f"Фотографий: <b>{len(media)}</b>"
    )
    kwargs = {}
    if media:
        kwargs = {
            "media_key": f"post:{order.id}:{index}",
            "photo_file_id": media[index]["file_id"],
        }
    else:
        kwargs = {"media_key": "main"}
    await render_user_screen(
        callback.bot,
        callback.from_user.id,
        caption,
        preview_markup(scope, order, index, back),
        source_message=callback.message,
        **kwargs,
    )


@router.callback_query(F.data.startswith("postpreview:"))
async def post_preview_page(callback: CallbackQuery) -> None:
    _, scope, raw_order, raw_index = callback.data.split(":", 3)
    order_id, index = int(raw_order), int(raw_index)
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
        if not order:
            await callback.answer("Заказ не найден.", show_alert=True)
            return
        if scope == "buyer" and order.user_id != callback.from_user.id:
            await callback.answer("Доступ запрещён.", show_alert=True)
            return
        if scope == "admin" and not await is_admin(session, callback.from_user.id):
            await callback.answer("Доступ запрещён.", show_alert=True)
            return
    await render_post_preview(callback, scope, order, index)
    await callback.answer()


@router.callback_query(F.data.startswith("buyerorder:show:"))
async def buyer_show(callback: CallbackQuery) -> None:
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
    if not order or order.user_id != callback.from_user.id:
        await callback.answer("Заказ не найден.", show_alert=True)
        return
    await render_post_preview(callback, "buyer", order, 0)
    await callback.answer()


@router.callback_query(F.data.startswith(("adminv5:show_order:", "activev3:show:")))
async def admin_show(callback: CallbackQuery) -> None:
    if not await admin_allowed(callback):
        return
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return
    await render_post_preview(callback, "admin", order, 0)
    await callback.answer()


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()
