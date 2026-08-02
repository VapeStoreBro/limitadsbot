from html import escape

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, select

from app.config import get_settings
from app.db.session import SessionFactory
from app.enums import OrderStatus
from app.keyboards_v3 import (
    client_actions_keyboard,
    client_block_confirmation,
    home_keyboard,
)
from app.models import AdOrder, User
from app.services.blocking import block_user, get_user_block, unblock_user
from app.services.telegram_ads import finish_order
from app.services.users import is_admin

router = Router(name="client_controls_v4")
settings = get_settings()


async def require_admin(callback: CallbackQuery) -> bool:
    if callback.message and callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer("Управление клиентами доступно только в личке бота.", show_alert=True)
        return False
    async with SessionFactory() as session:
        allowed = await is_admin(session, callback.from_user.id)
    if not allowed:
        await callback.answer("Доступ запрещён.", show_alert=True)
    return allowed


@router.callback_query(F.data.startswith("adminv3:client:"))
async def client_card_v4(callback: CallbackQuery) -> None:
    if not await require_admin(callback):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        user = await session.get(User, user_id)
        block = await get_user_block(session, user_id)
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

    history = ", ".join(f"@{escape(value)}" for value in user.username_history or []) or "нет"
    active_text = ", ".join(f"№{order.id} {order.tariff_code}" for order in active) or "нет"
    access = (
        f"🚫 Заблокирован: {escape(block.reason)}"
        if block
        else "✅ Доступ разрешён"
    )
    await callback.answer("Карточка клиента")
    await callback.bot.send_message(
        callback.from_user.id,
        f"<b><u>👤 КЛИЕНТ</u></b>\n\n"
        f"├ Имя: <a href=\"tg://user?id={user.id}\"><b>{escape(user.first_name or str(user.id))}</b></a>\n"
        f"├ ID: <code>{user.id}</code>\n"
        f"├ Username: @{escape(user.username) if user.username else 'нет'}\n"
        f"├ Старые username: {history}\n"
        f"├ Телефон: <code>{escape(user.phone or 'не указан')}</code>\n"
        f"├ Статус в группе: <b>{escape(user.bazaar_status or 'unknown')}</b>\n"
        f"├ Заказов: <b>{orders_count or 0}</b>\n"
        f"├ Оплачено: <b>{int(paid or 0)} ₽</b>\n"
        f"├ Активные рекламы: <b>{escape(active_text)}</b>\n"
        f"└ Доступ: <b>{access}</b>",
        reply_markup=client_actions_keyboard(user.id, blocked=bool(block)),
    )


@router.callback_query(F.data.startswith("clientv4:orders:"))
async def client_orders_v4(callback: CallbackQuery) -> None:
    if not await require_admin(callback):
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
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")])
    await callback.answer()
    await callback.bot.send_message(
        callback.from_user.id,
        f"<b>📦 Заказы клиента <code>{user_id}</code></b>\n\n"
        + ("Выберите заказ." if orders else "У клиента пока нет заказов."),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("clientv4:block_confirm:"))
async def client_block_confirm(callback: CallbackQuery) -> None:
    if not await require_admin(callback):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    if user_id == settings.owner_id:
        await callback.answer("Владельца блокировать нельзя.", show_alert=True)
        return
    await callback.answer()
    await callback.bot.send_message(
        callback.from_user.id,
        "<b>⚠️ Заблокировать клиента?</b>\n\n"
        "Все активные рекламы клиента будут немедленно завершены: закреп снимется, "
        "Best-публикации остановятся, префикс удалится или пересчитается. Незавершённые заявки отменятся.",
        reply_markup=client_block_confirmation(user_id),
    )


@router.callback_query(F.data.startswith("clientv4:block:"))
async def client_block(callback: CallbackQuery) -> None:
    if not await require_admin(callback):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    if user_id == settings.owner_id:
        await callback.answer("Владельца блокировать нельзя.", show_alert=True)
        return
    async with SessionFactory() as session:
        user = await session.get(User, user_id)
        if not user:
            await callback.answer("Клиент не найден.", show_alert=True)
            return
        await block_user(
            session,
            callback.bot,
            user_id,
            callback.from_user.id,
            "Доступ ограничен администрацией",
        )
    try:
        await callback.bot.send_message(
            user_id,
            "<b>🚫 Доступ к покупке рекламы ограничен администрацией</b>\n\n"
            "Активные размещения остановлены, закреп и префикс сняты. Для уточнения свяжитесь с администрацией.",
            reply_markup=home_keyboard(),
        )
    except Exception:
        pass
    await callback.answer("Клиент заблокирован", show_alert=True)
    await callback.bot.send_message(
        callback.from_user.id,
        f"✅ Клиент <code>{user_id}</code> заблокирован. Активные рекламы остановлены.",
        reply_markup=home_keyboard("adminv3:clients"),
    )


@router.callback_query(F.data.startswith("clientv4:unblock:"))
async def client_unblock(callback: CallbackQuery) -> None:
    if not await require_admin(callback):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        changed = await unblock_user(session, user_id)
    if changed:
        try:
            await callback.bot.send_message(
                user_id,
                "<b>✅ Доступ к покупке рекламы восстановлен</b>",
                reply_markup=home_keyboard(),
            )
        except Exception:
            pass
    await callback.answer("Доступ восстановлен" if changed else "Клиент не был заблокирован", show_alert=True)


@router.callback_query(F.data.startswith("activev3:"))
async def guard_blocked_active_actions(callback: CallbackQuery) -> None:
    """Prevent extending or republishing an ad after its owner was blocked."""
    parts = callback.data.split(":")
    if len(parts) < 3 or parts[1] in {"view", "show", "stop", "stop_confirm"}:
        raise SkipHandler
    try:
        order_id = int(parts[2])
    except ValueError:
        raise SkipHandler

    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
        block = await get_user_block(session, order.user_id) if order else None
        if not order or not block:
            raise SkipHandler
        if order.status == OrderStatus.ACTIVE.value:
            await finish_order(
                session,
                callback.bot,
                order,
                status=OrderStatus.CANCELLED.value,
            )
    await callback.answer(
        "Клиент заблокирован. Реклама завершена, закреп и префикс сняты.",
        show_alert=True,
    )
