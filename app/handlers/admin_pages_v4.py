from html import escape

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, select

from app.config import get_settings
from app.db.session import SessionFactory
from app.enums import OrderStatus
from app.keyboards import DURATION_NAMES, TARIFF_NAMES, admin_management_keyboard
from app.models import AdOrder, Admin, Payment, TariffPrice, User, UserPrice
from app.services.users import is_admin

router = Router(name="admin_pages_v4")
settings = get_settings()


def admin_nav(extra_rows: list[list[InlineKeyboardButton]] | None = None) -> InlineKeyboardMarkup:
    rows = list(extra_rows or [])
    rows.append(
        [
            InlineKeyboardButton(text="⬅️ Админ-панель", callback_data="profile:admin"),
            InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def require_admin(callback: CallbackQuery) -> bool:
    if callback.message and callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer("Раздел доступен только в личке бота.", show_alert=True)
        return False
    async with SessionFactory() as session:
        allowed = await is_admin(session, callback.from_user.id)
    if not allowed:
        await callback.answer("Доступ запрещён.", show_alert=True)
    return allowed


@router.callback_query(F.data == "adminv3:bookings")
async def bookings(callback: CallbackQuery) -> None:
    if not await require_admin(callback):
        return
    async with SessionFactory() as session:
        orders = (
            await session.scalars(
                select(AdOrder)
                .where(AdOrder.status == OrderStatus.BOOKED.value)
                .order_by(AdOrder.requested_start_at)
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
    await callback.answer()
    await callback.bot.send_message(
        callback.from_user.id,
        "<b><u>📅 БРОНИРОВАНИЯ</u></b>\n\n"
        + ("Выберите бронь." if orders else "Активных броней нет."),
        reply_markup=admin_nav(rows),
    )


@router.callback_query(F.data == "adminv3:clients")
async def clients(callback: CallbackQuery) -> None:
    if not await require_admin(callback):
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
    await callback.answer()
    await callback.bot.send_message(
        callback.from_user.id,
        "<b><u>👥 КЛИЕНТЫ</u></b>\n\nВыберите клиента для управления.",
        reply_markup=admin_nav(rows),
    )


@router.callback_query(F.data == "adminv3:payments")
async def payments(callback: CallbackQuery) -> None:
    if not await require_admin(callback):
        return
    async with SessionFactory() as session:
        rows = (
            await session.scalars(select(Payment).order_by(Payment.id.desc()).limit(30))
        ).all()
    lines = ["<b><u>💳 ПЛАТЕЖИ</u></b>"]
    for payment in rows:
        lines.append(
            f"\n№{payment.id} · заказ №{payment.order_id} · <b>{payment.amount_rub} ₽</b> · "
            f"{escape(payment.provider)} · {escape(payment.status)}"
        )
    if not rows:
        lines.append("\nПлатежей пока нет.")
    await callback.answer()
    await callback.bot.send_message(
        callback.from_user.id,
        "\n".join(lines),
        reply_markup=admin_nav(),
    )


@router.callback_query(F.data == "adminv3:tariffs")
async def tariffs(callback: CallbackQuery) -> None:
    if not await require_admin(callback):
        return
    async with SessionFactory() as session:
        prices = (await session.scalars(select(TariffPrice).order_by(TariffPrice.id))).all()
    lines = ["<b><u>🏷 ТАРИФЫ</u></b>"]
    for row in prices:
        lines.append(
            f"\n{TARIFF_NAMES.get(row.tariff_code, row.tariff_code)} · "
            f"{DURATION_NAMES.get(row.duration_code, row.duration_code)} — <b>{row.price_rub} ₽</b>"
        )
    await callback.answer()
    await callback.bot.send_message(
        callback.from_user.id,
        "\n".join(lines),
        reply_markup=admin_nav(),
    )


@router.callback_query(F.data == "adminv3:admins")
async def admins(callback: CallbackQuery) -> None:
    if not await require_admin(callback):
        return
    async with SessionFactory() as session:
        rows = (await session.scalars(select(Admin).order_by(Admin.added_at))).all()
    text = "<b><u>👮 АДМИНИСТРАТОРЫ</u></b>\n\n" + "\n".join(
        f"<code>{row.user_id}</code> · {escape(row.role)}" for row in rows
    )
    if callback.from_user.id == settings.owner_id:
        manage = admin_management_keyboard().inline_keyboard[:-1]
        markup = admin_nav(manage)
    else:
        markup = admin_nav()
    await callback.answer()
    await callback.bot.send_message(callback.from_user.id, text, reply_markup=markup)


@router.callback_query(F.data == "adminv3:stats")
async def stats(callback: CallbackQuery) -> None:
    if not await require_admin(callback):
        return
    async with SessionFactory() as session:
        users = await session.scalar(select(func.count()).select_from(User))
        orders = await session.scalar(select(func.count()).select_from(AdOrder))
        active = await session.scalar(
            select(func.count()).select_from(AdOrder).where(
                AdOrder.status == OrderStatus.ACTIVE.value
            )
        )
        paid = await session.scalar(select(func.coalesce(func.sum(AdOrder.paid_rub), 0)))
    await callback.answer()
    await callback.bot.send_message(
        callback.from_user.id,
        "<b><u>📊 СТАТИСТИКА</u></b>\n\n"
        f"├ Пользователей: <b>{users or 0}</b>\n"
        f"├ Заказов: <b>{orders or 0}</b>\n"
        f"├ Активных реклам: <b>{active or 0}</b>\n"
        f"└ Оплачено: <b>{int(paid or 0)} ₽</b>",
        reply_markup=admin_nav(),
    )


@router.callback_query(F.data == "adminv3:prices")
async def prices(callback: CallbackQuery, state: FSMContext) -> None:
    if not await require_admin(callback):
        return
    await state.clear()
    async with SessionFactory() as session:
        rows = (
            await session.scalars(
                select(UserPrice).order_by(UserPrice.updated_at.desc()).limit(20)
            )
        ).all()
    lines = ["<b><u>💰 ПЕРСОНАЛЬНЫЕ ЦЕНЫ</u></b>"]
    for row in rows:
        lines.append(
            f"\n<code>{row.user_id}</code> · {row.tariff_code}/{row.duration_code} — "
            f"<b>{row.price_rub} ₽</b>"
        )
    if not rows:
        lines.append("\nПерсональных цен пока нет.")
    await callback.answer()
    await callback.bot.send_message(
        callback.from_user.id,
        "\n".join(lines),
        reply_markup=admin_nav(
            [[
                InlineKeyboardButton(
                    text="➕ Назначить цену",
                    callback_data="pricev3:add",
                    style="success",
                )
            ]]
        ),
    )
