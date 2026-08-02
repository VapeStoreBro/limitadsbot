from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import func, select

from app.config import get_settings
from app.db.session import SessionFactory
from app.enums import DurationCode, OrderStatus, TariffCode
from app.keyboards import admin_menu, customer_menu
from app.models import AdOrder, Admin, TariffPrice, User, UserPrice
from app.services.users import is_admin
from app.states import AdminFlow

router = Router(name="admin")
settings = get_settings()


async def require_admin(message: Message) -> bool:
    if not message.from_user:
        return False
    async with SessionFactory() as session:
        allowed = await is_admin(session, message.from_user.id)
    if not allowed:
        await message.answer("Доступ запрещён.")
    return allowed


@router.message(F.text == "🔐 Админ-панель")
async def open_admin(message: Message) -> None:
    if await require_admin(message):
        await message.answer("🔐 Панель управления", reply_markup=admin_menu())


@router.message(F.text == "⬅️ Меню покупателя")
async def close_admin(message: Message) -> None:
    async with SessionFactory() as session:
        admin = bool(message.from_user and await is_admin(session, message.from_user.id))
    await message.answer("Главное меню", reply_markup=customer_menu(admin))


@router.message(F.text == "📥 Новые заявки")
async def new_orders(message: Message) -> None:
    if not await require_admin(message):
        return
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
        await message.answer("Новых заявок нет.")
        return
    await message.answer(
        "<b>Заявки на модерации:</b>\n"
        + "\n".join(f"№{row.id} · {row.tariff_code} · клиент {row.user_id}" for row in rows)
    )


@router.message(F.text == "📢 Активная реклама")
async def active_orders(message: Message) -> None:
    if not await require_admin(message):
        return
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
    await message.answer(
        "<b>Активная реклама:</b>\n"
        + "\n".join(
            f"№{row.id} · {row.tariff_code} · клиент {row.user_id} · до {row.ends_at}"
            for row in rows
        )
    )


@router.message(F.text == "📅 Бронирования")
async def bookings(message: Message) -> None:
    if not await require_admin(message):
        return
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
    await message.answer(
        "<b>Бронирования:</b>\n"
        + "\n".join(
            f"№{row.id} · {row.tariff_code} · {row.requested_start_at} · "
            f"{row.paid_rub}/{row.price_rub} ₽"
            for row in rows
        )
    )


@router.message(F.text == "👥 Клиенты")
async def clients(message: Message) -> None:
    if not await require_admin(message):
        return
    async with SessionFactory() as session:
        rows = (
            await session.scalars(select(User).order_by(User.last_seen_at.desc()).limit(20))
        ).all()
    if not rows:
        await message.answer("Клиентов пока нет.")
        return
    lines = ["<b>Последние клиенты:</b>"]
    for user in rows:
        history = ", ".join(f"@{value}" for value in user.username_history or []) or "нет"
        lines.append(
            f"\n<a href=\"tg://user?id={user.id}\">{user.first_name}</a>"
            f"\nID: <code>{user.id}</code>"
            f"\nUsername: @{user.username or 'нет'}"
            f"\nИстория username: {history}"
            f"\nТелефон: {user.phone or 'не указан'}"
            f"\nСтатус в группе: {user.bazaar_status or 'unknown'}"
            f"\nПоследняя активность: {user.last_seen_at}"
        )
    await message.answer("\n".join(lines))


@router.message(F.text == "🏷 Тарифы")
async def admin_tariffs(message: Message) -> None:
    if not await require_admin(message):
        return
    async with SessionFactory() as session:
        rows = (await session.scalars(select(TariffPrice).order_by(TariffPrice.id))).all()
    await message.answer(
        "<b>Базовые цены:</b>\n"
        + "\n".join(
            f"{row.tariff_code}/{row.duration_code}: {row.price_rub} ₽ · {row.duration_hours} ч."
            for row in rows
        )
    )


@router.message(F.text == "💰 Персональные цены")
async def personal_prices(message: Message, state: FSMContext) -> None:
    if not await require_admin(message):
        return
    await state.set_state(AdminFlow.setting_personal_price)
    await message.answer(
        "Введите: <code>ID тариф период цена процент</code>\n"
        "Пример: <code>123456 middle month 1700 15</code>. Процент можно указать 0."
    )


@router.message(AdminFlow.setting_personal_price, F.text)
async def set_personal_price(message: Message, state: FSMContext) -> None:
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
        await message.answer("Неверный формат или неизвестный тариф/период.")
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
        f"🎁 Для вас цена {tariff}/{duration}: {price} ₽"
        + (f" (объявленная скидка {percent}%)." if percent else "."),
    )
    await message.answer("✅ Цена сохранена.")


@router.message(F.text == "👮 Администраторы")
async def administrators(message: Message, state: FSMContext) -> None:
    if not await require_admin(message):
        return
    async with SessionFactory() as session:
        rows = (await session.scalars(select(Admin).order_by(Admin.added_at))).all()
    text = "<b>Администраторы:</b>\n" + "\n".join(
        f"<code>{row.user_id}</code> · {row.role}" for row in rows
    )
    if message.from_user.id == settings.owner_id:
        text += "\n\nОтправьте <code>добавить ID</code> или <code>удалить ID</code>."
        await state.set_state(AdminFlow.adding_admin)
    await message.answer(text)


@router.message(AdminFlow.adding_admin, F.text)
async def manage_admin(message: Message, state: FSMContext) -> None:
    if message.from_user.id != settings.owner_id:
        await state.clear()
        return
    try:
        action, raw_id = message.text.lower().split()
        user_id = int(raw_id)
    except ValueError:
        await message.answer("Формат: <code>добавить ID</code> или <code>удалить ID</code>.")
        return

    async with SessionFactory() as session:
        if action == "добавить":
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
        elif action == "удалить" and user_id != settings.owner_id:
            row = await session.get(Admin, user_id)
            if row:
                await session.delete(row)
        else:
            await message.answer("Нельзя удалить владельца или команда неизвестна.")
            return
        await session.commit()

    await state.clear()
    await message.answer("✅ Список администраторов обновлён.")


@router.message(F.text == "📊 Статистика")
async def statistics(message: Message) -> None:
    if not await require_admin(message):
        return
    async with SessionFactory() as session:
        users = await session.scalar(select(func.count()).select_from(User))
        orders = await session.scalar(select(func.count()).select_from(AdOrder))
        paid = await session.scalar(select(func.coalesce(func.sum(AdOrder.paid_rub), 0)))
        active = await session.scalar(
            select(func.count()).select_from(AdOrder).where(
                AdOrder.status == OrderStatus.ACTIVE.value
            )
        )
    await message.answer(
        f"👥 Пользователей: {users}\n"
        f"📦 Заказов: {orders}\n"
        f"🚀 Активных: {active}\n"
        f"💰 Тестовых оплат: {paid} ₽"
    )
