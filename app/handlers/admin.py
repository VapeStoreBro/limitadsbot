from datetime import datetime, timezone
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)
from sqlalchemy import func, select

from app.config import get_settings
from app.db.session import SessionFactory
from app.enums import DurationCode, OrderStatus, TariffCode
from app.keyboards import admin_management_keyboard, admin_menu
from app.models import AdOrder, Admin, Payment, TariffPrice, User, UserPrice
from app.services.users import is_admin
from app.states import AdminFlow

router = Router(name="admin")
settings = get_settings()


async def require_admin_id(user_id: int) -> bool:
    async with SessionFactory() as session:
        return await is_admin(session, user_id)


async def require_admin(message: Message) -> bool:
    if not message.from_user:
        return False
    allowed = await require_admin_id(message.from_user.id)
    if not allowed:
        await message.answer("Доступ запрещён.")
    return allowed


@router.callback_query(F.data == "profile:admin")
async def open_admin_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not await require_admin_id(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await state.clear()
    await callback.message.answer("🔐 <b>Панель управления</b>", reply_markup=admin_menu())
    await callback.answer()


@router.message(F.text == "🔐 Админ-панель")
async def open_admin(message: Message, state: FSMContext) -> None:
    if await require_admin(message):
        await state.clear()
        await message.answer("🔐 <b>Панель управления</b>", reply_markup=admin_menu())


@router.message(F.text == "⬅️ Профиль")
async def close_admin(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    await state.clear()
    async with SessionFactory() as session:
        user = await session.get(User, message.from_user.id)
        admin = await is_admin(session, message.from_user.id)
    await message.answer("Профиль", reply_markup=ReplyKeyboardRemove())
    if user:
        from app.handlers.common import show_profile

        await show_profile(message.bot, message.chat.id, user, admin)


@router.message(F.text == "📥 Заявки")
async def new_orders(message: Message, state: FSMContext) -> None:
    if not await require_admin(message):
        return
    await state.clear()
    async with SessionFactory() as session:
        rows = (
            await session.scalars(
                select(AdOrder)
                .where(AdOrder.status == OrderStatus.MODERATION.value)
                .order_by(AdOrder.id.desc())
                .limit(20)
            )
        ).all()
    if not rows:
        await message.answer("✅ Новых заявок нет.")
        return
    lines = ["<b>📥 Заявки на модерации</b>"]
    for row in rows:
        lines.append(
            f"\n№{row.id} · <b>{row.tariff_code}</b> · {row.price_rub} ₽"
            f"\nКлиент: <a href=\"tg://user?id={row.user_id}\">{row.user_id}</a>"
        )
    await message.answer("\n".join(lines))


@router.message(F.text == "🚀 Активная реклама")
async def active_orders(message: Message, state: FSMContext) -> None:
    if not await require_admin(message):
        return
    await state.clear()
    async with SessionFactory() as session:
        rows = (
            await session.scalars(
                select(AdOrder)
                .where(AdOrder.status == OrderStatus.ACTIVE.value)
                .order_by(AdOrder.ends_at)
            )
        ).all()
    if not rows:
        await message.answer("Активной рекламы нет.")
        return
    lines = ["<b>🚀 Активная реклама</b>"]
    for row in rows:
        lines.append(
            f"\n№{row.id} · <b>{row.tariff_code}</b>"
            f"\nКлиент: <a href=\"tg://user?id={row.user_id}\">{row.user_id}</a>"
            f"\nДействует до: <code>{row.ends_at}</code>"
        )
    await message.answer("\n".join(lines))


@router.message(F.text == "📅 Бронирования")
async def bookings(message: Message, state: FSMContext) -> None:
    if not await require_admin(message):
        return
    await state.clear()
    async with SessionFactory() as session:
        rows = (
            await session.scalars(
                select(AdOrder)
                .where(AdOrder.status == OrderStatus.BOOKED.value)
                .order_by(AdOrder.requested_start_at)
            )
        ).all()
    if not rows:
        await message.answer("Бронирований нет.")
        return
    lines = ["<b>📅 Бронирования</b>"]
    for row in rows:
        lines.append(
            f"\n№{row.id} · <b>{row.tariff_code}</b>"
            f"\nЗапуск: <code>{row.requested_start_at}</code>"
            f"\nОплачено: <b>{row.paid_rub}/{row.price_rub} ₽</b>"
        )
    await message.answer("\n".join(lines))


@router.message(F.text == "👥 Клиенты")
async def clients(message: Message, state: FSMContext) -> None:
    if not await require_admin(message):
        return
    await state.clear()
    async with SessionFactory() as session:
        rows = (
            await session.scalars(select(User).order_by(User.last_seen_at.desc()).limit(12))
        ).all()
        totals = {
            user.id: await session.scalar(
                select(func.coalesce(func.sum(AdOrder.paid_rub), 0)).where(
                    AdOrder.user_id == user.id
                )
            )
            for user in rows
        }
    if not rows:
        await message.answer("Клиентов пока нет.")
        return
    lines = ["<b>👥 Последние клиенты</b>"]
    for user in rows:
        history = ", ".join(f"@{escape(value)}" for value in user.username_history or []) or "нет"
        full_name = " ".join(part for part in [user.first_name, user.last_name] if part)
        lines.append(
            f"\n<a href=\"tg://user?id={user.id}\"><b>{escape(full_name or str(user.id))}</b></a>"
            f"\nID: <code>{user.id}</code> · @{escape(user.username) if user.username else 'нет'}"
            f"\nТелефон: <code>{escape(user.phone or 'не указан')}</code>"
            f"\nСтатус: <b>{escape(user.bazaar_status or 'unknown')}</b>"
            f"\nПотрачено: <b>{int(totals[user.id] or 0)} ₽</b>"
            f"\nUsername раньше: {history}"
        )
    await message.answer("\n".join(lines))


@router.message(F.text == "💳 Платежи")
async def payments(message: Message, state: FSMContext) -> None:
    if not await require_admin(message):
        return
    await state.clear()
    async with SessionFactory() as session:
        rows = (
            await session.scalars(select(Payment).order_by(Payment.id.desc()).limit(20))
        ).all()
        orders = {
            row.order_id: await session.get(AdOrder, row.order_id)
            for row in rows
        }
    if not rows:
        await message.answer("Платежей пока нет.")
        return
    lines = ["<b>💳 Последние платежи</b>"]
    for row in rows:
        order = orders.get(row.order_id)
        user_id = order.user_id if order else "?"
        lines.append(
            f"\nПлатёж №{row.id} · заказ №{row.order_id}"
            f"\nКлиент: <code>{user_id}</code>"
            f"\nСумма: <b>{row.amount_rub} ₽</b> · {row.provider} · {row.status}"
        )
    await message.answer("\n".join(lines))


@router.message(F.text == "🏷 Тарифы")
async def admin_tariffs(message: Message, state: FSMContext) -> None:
    if not await require_admin(message):
        return
    await state.clear()
    async with SessionFactory() as session:
        rows = (await session.scalars(select(TariffPrice).order_by(TariffPrice.id))).all()
    await message.answer(
        "<b>🏷 Тарифы и цены</b>\n\n"
        + "\n".join(
            f"{row.tariff_code}/{row.duration_code}: <b>{row.price_rub} ₽</b> · {row.duration_hours} ч."
            for row in rows
        )
    )


def personal_price_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Назначить персональную цену",
                    callback_data="price:setup",
                    style="success",
                )
            ]
        ]
    )


@router.message(F.text == "💰 Персональные цены")
async def personal_prices(message: Message, state: FSMContext) -> None:
    if not await require_admin(message):
        return
    await state.clear()
    async with SessionFactory() as session:
        rows = (
            await session.scalars(
                select(UserPrice).order_by(UserPrice.updated_at.desc()).limit(20)
            )
        ).all()
    lines = ["<b>💰 Персональные цены</b>"]
    if rows:
        for row in rows:
            lines.append(
                f"\n<code>{row.user_id}</code> · {row.tariff_code}/{row.duration_code}"
                f" · <b>{row.price_rub} ₽</b>"
                + (
                    f" · скидка {row.announced_discount_percent}%"
                    if row.announced_discount_percent
                    else ""
                )
            )
    else:
        lines.append("\nПока не назначены.")
    await message.answer("\n".join(lines), reply_markup=personal_price_keyboard())


@router.callback_query(F.data == "price:setup")
async def begin_personal_price(callback: CallbackQuery, state: FSMContext) -> None:
    if not await require_admin_id(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return
    await state.set_state(AdminFlow.setting_personal_price)
    await callback.message.answer(
        "Введите одной строкой:\n"
        "<code>ID тариф период цена процент</code>\n\n"
        "Пример: <code>123456 middle month 1700 15</code>\n"
        "Процент укажите 0, если объявлять скидку не нужно."
    )
    await callback.answer()


@router.message(AdminFlow.setting_personal_price, F.text)
async def set_personal_price(message: Message, state: FSMContext) -> None:
    if message.text in {
        "📥 Заявки",
        "🚀 Активная реклама",
        "📅 Бронирования",
        "👥 Клиенты",
        "💳 Платежи",
        "💰 Персональные цены",
        "🏷 Тарифы",
        "👮 Администраторы",
        "📊 Статистика",
        "⬅️ Профиль",
    }:
        await state.clear()
        return
    try:
        raw_id, tariff, duration, raw_price, raw_percent = message.text.split()
        user_id, price, percent = int(raw_id), int(raw_price), int(raw_percent)
        if tariff not in {item.value for item in TariffCode}:
            raise ValueError
        if duration not in {item.value for item in DurationCode}:
            raise ValueError
        if price < 0 or percent < 0:
            raise ValueError
    except ValueError:
        await message.answer("Неверный формат. Повторите или нажмите другую кнопку меню.")
        return

    now = datetime.now(timezone.utc)
    async with SessionFactory() as session:
        user = await session.get(User, user_id)
        if not user:
            await message.answer("Пользователь сначала должен открыть бота.")
            return
        row = await session.scalar(
            select(UserPrice).where(
                UserPrice.user_id == user_id,
                UserPrice.tariff_code == tariff,
                UserPrice.duration_code == duration,
            )
        )
        if row is None:
            row = UserPrice(
                user_id=user_id,
                tariff_code=tariff,
                duration_code=duration,
                price_rub=price,
                announced_discount_percent=percent or None,
                updated_by=message.from_user.id,
                updated_at=now,
            )
            session.add(row)
        else:
            row.price_rub = price
            row.announced_discount_percent = percent or None
            row.updated_by = message.from_user.id
            row.updated_at = now
        await session.commit()

    await state.clear()
    await message.bot.send_message(
        user_id,
        f"🎁 Для вас установлена цена {tariff}/{duration}: <b>{price} ₽</b>"
        + (f"\nСкидка: <b>{percent}%</b>" if percent else ""),
    )
    await message.answer("✅ Персональная цена сохранена.")


@router.message(F.text == "👮 Администраторы")
async def administrators(message: Message, state: FSMContext) -> None:
    if not await require_admin(message):
        return
    await state.clear()
    async with SessionFactory() as session:
        rows = (await session.scalars(select(Admin).order_by(Admin.added_at))).all()
    text = "<b>👮 Администраторы</b>\n\n" + "\n".join(
        f"<code>{row.user_id}</code> · {row.role}" for row in rows
    )
    markup = admin_management_keyboard() if message.from_user.id == settings.owner_id else None
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "admin:add")
async def begin_add_admin(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user.id != settings.owner_id:
        await callback.answer("Только владелец может менять администраторов.", show_alert=True)
        return
    await state.set_state(AdminFlow.adding_admin)
    await callback.message.answer("Отправьте Telegram ID нового администратора.")
    await callback.answer()


@router.callback_query(F.data == "admin:remove")
async def begin_remove_admin(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user.id != settings.owner_id:
        await callback.answer("Только владелец может менять администраторов.", show_alert=True)
        return
    await state.set_state(AdminFlow.removing_admin)
    await callback.message.answer("Отправьте Telegram ID администратора для удаления.")
    await callback.answer()


@router.message(AdminFlow.adding_admin, F.text)
async def add_admin(message: Message, state: FSMContext) -> None:
    if message.from_user.id != settings.owner_id:
        await state.clear()
        return
    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer("Нужен числовой Telegram ID.")
        return
    async with SessionFactory() as session:
        if not await session.get(User, user_id):
            await message.answer("Пользователь сначала должен открыть бота.")
            return
        if not await session.get(Admin, user_id):
            session.add(
                Admin(
                    user_id=user_id,
                    role="admin",
                    added_by=message.from_user.id,
                    added_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
    await state.clear()
    await message.answer("✅ Администратор добавлен.")


@router.message(AdminFlow.removing_admin, F.text)
async def remove_admin(message: Message, state: FSMContext) -> None:
    if message.from_user.id != settings.owner_id:
        await state.clear()
        return
    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer("Нужен числовой Telegram ID.")
        return
    if user_id == settings.owner_id:
        await message.answer("Владельца удалить нельзя.")
        return
    async with SessionFactory() as session:
        row = await session.get(Admin, user_id)
        if row:
            await session.delete(row)
            await session.commit()
    await state.clear()
    await message.answer("✅ Администратор удалён.")


@router.message(F.text == "📊 Статистика")
async def statistics(message: Message, state: FSMContext) -> None:
    if not await require_admin(message):
        return
    await state.clear()
    async with SessionFactory() as session:
        users = await session.scalar(select(func.count()).select_from(User))
        orders = await session.scalar(select(func.count()).select_from(AdOrder))
        paid = await session.scalar(select(func.coalesce(func.sum(AdOrder.paid_rub), 0)))
        active = await session.scalar(
            select(func.count())
            .select_from(AdOrder)
            .where(AdOrder.status == OrderStatus.ACTIVE.value)
        )
        booked = await session.scalar(
            select(func.count())
            .select_from(AdOrder)
            .where(AdOrder.status == OrderStatus.BOOKED.value)
        )
        moderation = await session.scalar(
            select(func.count())
            .select_from(AdOrder)
            .where(AdOrder.status == OrderStatus.MODERATION.value)
        )
    await message.answer(
        "<b>📊 Статистика</b>\n\n"
        f"👥 Пользователей: <b>{users}</b>\n"
        f"📦 Всего заказов: <b>{orders}</b>\n"
        f"🛡 На модерации: <b>{moderation}</b>\n"
        f"🚀 Активных: <b>{active}</b>\n"
        f"📅 Броней: <b>{booked}</b>\n"
        f"💰 Оплачено: <b>{paid} ₽</b>"
    )
