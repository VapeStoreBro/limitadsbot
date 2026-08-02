from datetime import datetime, timedelta, timezone
from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, or_, select

from app.config import get_settings
from app.db.session import SessionFactory
from app.enums import DurationCode, OrderStatus, TariffCode
from app.handlers.order_admin_v2 import send_order_list
from app.keyboards import DURATION_NAMES, TARIFF_NAMES, admin_management_keyboard
from app.keyboards_v3 import (
    active_ad_actions,
    active_ads_keyboard,
    active_stop_confirmation,
    admin_panel_keyboard,
    price_discount_keyboard,
    price_duration_keyboard,
    price_tariff_keyboard,
    settings_keyboard,
)
from app.models import AdOrder, Admin, Payment, TariffPrice, User, UserPrice
from app.services.app_settings import STAFF_CHAT_KEY, get_staff_chat_id, set_setting
from app.services.staff_delivery import send_ad_content_resilient, test_staff_chat
from app.services.telegram_ads import (
    finish_order,
    publish_best_copy,
    refresh_user_prefix,
)
from app.services.users import is_admin
from app.states import AdminFlow

router = Router(name="admin_panel_v3")
settings = get_settings()


async def admin_allowed(user_id: int) -> bool:
    async with SessionFactory() as session:
        return await is_admin(session, user_id)


async def require_private_admin(callback: CallbackQuery) -> bool:
    if callback.message and callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer("Управление доступно только в личке бота.", show_alert=True)
        return False
    if not await admin_allowed(callback.from_user.id):
        await callback.answer("Доступ запрещён.", show_alert=True)
        return False
    return True


@router.callback_query(F.data == "profile:admin")
async def open_admin_panel(callback: CallbackQuery, state: FSMContext) -> None:
    if not await require_private_admin(callback):
        return
    await state.clear()
    await callback.answer("Админ-панель")
    await callback.bot.send_message(
        callback.from_user.id,
        "<b><u>🔐 АДМИН-ПАНЕЛЬ</u></b>\n\n"
        "Все действия с заказами и активной рекламой выполняются здесь, в личке бота. "
        "Группа состава используется только для модерации новых заявок.",
        reply_markup=admin_panel_keyboard(),
    )


@router.callback_query(F.data == "adminv3:orders")
async def admin_orders(callback: CallbackQuery) -> None:
    if not await require_private_admin(callback):
        return
    await callback.answer()
    await send_order_list(callback.bot, callback.from_user.id)


@router.callback_query(F.data == "adminv3:active")
async def admin_active(callback: CallbackQuery) -> None:
    if not await require_private_admin(callback):
        return
    async with SessionFactory() as session:
        orders = (
            await session.scalars(
                select(AdOrder)
                .where(AdOrder.status == OrderStatus.ACTIVE.value)
                .order_by(AdOrder.ends_at)
            )
        ).all()
    await callback.answer()
    if not orders:
        await callback.bot.send_message(
            callback.from_user.id,
            "<b>🚀 Активная реклама</b>\n\nСейчас активных размещений нет.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Админ-панель", callback_data="profile:admin")]
                ]
            ),
        )
        return
    await callback.bot.send_message(
        callback.from_user.id,
        "<b><u>🚀 АКТИВНАЯ РЕКЛАМА</u></b>\n\n"
        "Откройте размещение, чтобы продлить, завершить, повторно закрепить или опубликовать Best сейчас.",
        reply_markup=active_ads_keyboard(orders),
    )


async def get_active_order(order_id: int) -> AdOrder | None:
    async with SessionFactory() as session:
        return await session.scalar(
            select(AdOrder).where(
                AdOrder.id == order_id,
                AdOrder.status == OrderStatus.ACTIVE.value,
            )
        )


def active_card(order: AdOrder) -> str:
    return (
        f"<b><u>🚀 АКТИВНАЯ РЕКЛАМА №{order.id}</u></b>\n\n"
        f"├ Клиент: <a href=\"tg://user?id={order.user_id}\"><code>{order.user_id}</code></a>\n"
        f"├ Тариф: <b>{TARIFF_NAMES.get(order.tariff_code, order.tariff_code)}</b>\n"
        f"├ Период: <b>{DURATION_NAMES.get(order.duration_code, order.duration_code)}</b>\n"
        f"├ Начало: <code>{order.activated_at:%d.%m.%Y %H:%M}</code>\n"
        f"├ Окончание: <code>{order.ends_at:%d.%m.%Y %H:%M}</code>\n"
        f"├ Закреп: <b>{'установлен' if order.pinned_message_id else 'нет'}</b>\n"
        f"└ Замены Middle: <b>{order.pin_changes_used}/2</b>"
    )


@router.callback_query(F.data.startswith("activev3:view:"))
async def active_view(callback: CallbackQuery) -> None:
    if not await require_private_admin(callback):
        return
    order_id = int(callback.data.rsplit(":", 1)[1])
    order = await get_active_order(order_id)
    if not order:
        await callback.answer("Реклама уже не активна.", show_alert=True)
        return
    await callback.answer("Открываю")
    await callback.bot.send_message(
        callback.from_user.id,
        active_card(order),
        reply_markup=active_ad_actions(order),
    )


@router.callback_query(F.data.startswith("activev3:show:"))
async def active_show(callback: CallbackQuery) -> None:
    if not await require_private_admin(callback):
        return
    order = await get_active_order(int(callback.data.rsplit(":", 1)[1]))
    if not order:
        await callback.answer("Реклама уже не активна.", show_alert=True)
        return
    await callback.answer("Показываю пост")
    await send_ad_content_resilient(callback.bot, callback.from_user.id, order)


@router.callback_query(F.data.startswith("activev3:extend:"))
async def active_extend(callback: CallbackQuery) -> None:
    if not await require_private_admin(callback):
        return
    _, _, raw_id, raw_hours = callback.data.split(":", 3)
    order_id, hours = int(raw_id), int(raw_hours)
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
        if not order or order.status != OrderStatus.ACTIVE.value or not order.ends_at:
            await callback.answer("Реклама уже не активна.", show_alert=True)
            return
        order.ends_at += timedelta(hours=hours)
        order.requested_end_at = order.ends_at
        order.duration_hours += hours
        order.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await refresh_user_prefix(session, callback.bot, order.user_id)
    await callback.answer(f"Продлено на {hours} ч.", show_alert=True)
    await callback.bot.send_message(
        order.user_id,
        f"<b>⏳ Реклама №{order.id} продлена</b>\n\n"
        f"Новая дата окончания: <code>{order.ends_at:%d.%m.%Y %H:%M}</code>.",
    )


@router.callback_query(F.data.startswith("activev3:repin:"))
async def active_repin(callback: CallbackQuery) -> None:
    if not await require_private_admin(callback):
        return
    order = await get_active_order(int(callback.data.rsplit(":", 1)[1]))
    if not order or not order.pinned_message_id:
        await callback.answer("У рекламы нет сохранённого закрепа.", show_alert=True)
        return
    try:
        await callback.bot.pin_chat_message(
            settings.bazaar_chat_id,
            order.pinned_message_id,
            disable_notification=True,
        )
        await callback.answer("Закреп восстановлен", show_alert=True)
    except Exception as error:
        await callback.answer(f"Telegram: {error}", show_alert=True)


@router.callback_query(F.data.startswith("activev3:publish:"))
async def active_publish(callback: CallbackQuery) -> None:
    if not await require_private_admin(callback):
        return
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
        if (
            not order
            or order.status != OrderStatus.ACTIVE.value
            or order.tariff_code != TariffCode.BEST.value
        ):
            await callback.answer("Действие доступно только активному Best.", show_alert=True)
            return
        await publish_best_copy(session, callback.bot, order)
    await callback.answer("Best опубликован", show_alert=True)


@router.callback_query(F.data.startswith("activev3:stop_confirm:"))
async def active_stop_confirm(callback: CallbackQuery) -> None:
    if not await require_private_admin(callback):
        return
    order_id = int(callback.data.rsplit(":", 1)[1])
    order = await get_active_order(order_id)
    if not order:
        await callback.answer("Реклама уже не активна.", show_alert=True)
        return
    await callback.answer()
    await callback.bot.send_message(
        callback.from_user.id,
        f"<b>⚠️ Завершить рекламу №{order.id} сейчас?</b>\n\n"
        "Закреп снимется, автоматизация остановится, сообщения останутся в группе.",
        reply_markup=active_stop_confirmation(order.id),
    )


@router.callback_query(F.data.startswith("activev3:stop:"))
async def active_stop(callback: CallbackQuery) -> None:
    if not await require_private_admin(callback):
        return
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
        if not order or order.status != OrderStatus.ACTIVE.value:
            await callback.answer("Реклама уже завершена.", show_alert=True)
            return
        await finish_order(session, callback.bot, order)
    await callback.bot.send_message(
        order.user_id,
        f"<b>🏁 Реклама №{order.id} завершена администрацией</b>",
    )
    await callback.message.edit_text(f"✅ Реклама №{order.id} завершена.")
    await callback.answer("Завершено")


@router.callback_query(F.data == "adminv3:bookings")
async def admin_bookings(callback: CallbackQuery) -> None:
    if not await require_private_admin(callback):
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
    rows.append([InlineKeyboardButton(text="⬅️ Админ-панель", callback_data="profile:admin")])
    await callback.answer()
    await callback.bot.send_message(
        callback.from_user.id,
        "<b>📅 Бронирования</b>\n\n" + ("Выберите бронь." if orders else "Активных броней нет."),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data == "adminv3:clients")
async def admin_clients(callback: CallbackQuery) -> None:
    if not await require_private_admin(callback):
        return
    async with SessionFactory() as session:
        users = (
            await session.scalars(select(User).order_by(User.last_seen_at.desc()).limit(25))
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
    rows.append([InlineKeyboardButton(text="⬅️ Админ-панель", callback_data="profile:admin")])
    await callback.answer()
    await callback.bot.send_message(
        callback.from_user.id,
        "<b>👥 Клиенты</b>\n\nВыберите клиента.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("adminv3:client:"))
async def admin_client_card(callback: CallbackQuery) -> None:
    if not await require_private_admin(callback):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        user = await session.get(User, user_id)
        orders_count = await session.scalar(
            select(func.count()).select_from(AdOrder).where(AdOrder.user_id == user_id)
        )
        paid = await session.scalar(
            select(func.coalesce(func.sum(AdOrder.paid_rub), 0)).where(AdOrder.user_id == user_id)
        )
        active = await session.scalar(
            select(AdOrder).where(
                AdOrder.user_id == user_id,
                AdOrder.status == OrderStatus.ACTIVE.value,
            )
        )
    if not user:
        await callback.answer("Клиент не найден.", show_alert=True)
        return
    history = ", ".join(f"@{escape(value)}" for value in user.username_history or []) or "нет"
    await callback.answer()
    await callback.bot.send_message(
        callback.from_user.id,
        f"<b><u>👤 КЛИЕНТ</u></b>\n\n"
        f"├ Имя: <a href=\"tg://user?id={user.id}\"><b>{escape(user.first_name)}</b></a>\n"
        f"├ ID: <code>{user.id}</code>\n"
        f"├ Username: @{escape(user.username) if user.username else 'нет'}\n"
        f"├ Старые username: {history}\n"
        f"├ Телефон: <code>{escape(user.phone or 'не указан')}</code>\n"
        f"├ Статус в группе: <b>{escape(user.bazaar_status or 'unknown')}</b>\n"
        f"├ Заказов: <b>{orders_count or 0}</b>\n"
        f"├ Оплачено: <b>{int(paid or 0)} ₽</b>\n"
        f"└ Активный тариф: <b>{active.tariff_code if active else 'нет'}</b>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💰 Назначить цену",
                        callback_data=f"pricev3:user:{user.id}",
                        style="primary",
                    )
                ],
                [InlineKeyboardButton(text="⬅️ К клиентам", callback_data="adminv3:clients")],
            ]
        ),
    )


@router.callback_query(F.data == "adminv3:payments")
async def admin_payments(callback: CallbackQuery) -> None:
    if not await require_private_admin(callback):
        return
    async with SessionFactory() as session:
        payments = (
            await session.scalars(select(Payment).order_by(Payment.id.desc()).limit(30))
        ).all()
    lines = ["<b><u>💳 ПЛАТЕЖИ</u></b>"]
    for payment in payments:
        lines.append(
            f"\n№{payment.id} · заказ №{payment.order_id} · <b>{payment.amount_rub} ₽</b> · {payment.provider} · {payment.status}"
        )
    if not payments:
        lines.append("\nПлатежей пока нет.")
    await callback.answer()
    await callback.bot.send_message(callback.from_user.id, "\n".join(lines))


@router.callback_query(F.data == "adminv3:tariffs")
async def admin_tariffs(callback: CallbackQuery) -> None:
    if not await require_private_admin(callback):
        return
    async with SessionFactory() as session:
        prices = (await session.scalars(select(TariffPrice).order_by(TariffPrice.id))).all()
    lines = ["<b><u>🏷 ТАРИФЫ</u></b>"]
    for row in prices:
        lines.append(
            f"\n{TARIFF_NAMES.get(row.tariff_code, row.tariff_code)} · {DURATION_NAMES.get(row.duration_code, row.duration_code)} — <b>{row.price_rub} ₽</b>"
        )
    await callback.answer()
    await callback.bot.send_message(callback.from_user.id, "\n".join(lines))


@router.callback_query(F.data == "adminv3:admins")
async def admin_admins(callback: CallbackQuery) -> None:
    if not await require_private_admin(callback):
        return
    async with SessionFactory() as session:
        admins = (await session.scalars(select(Admin).order_by(Admin.added_at))).all()
    text = "<b><u>👮 АДМИНИСТРАТОРЫ</u></b>\n\n" + "\n".join(
        f"<code>{row.user_id}</code> · {row.role}" for row in admins
    )
    await callback.answer()
    await callback.bot.send_message(
        callback.from_user.id,
        text,
        reply_markup=admin_management_keyboard() if callback.from_user.id == settings.owner_id else None,
    )


@router.callback_query(F.data == "adminv3:stats")
async def admin_stats(callback: CallbackQuery) -> None:
    if not await require_private_admin(callback):
        return
    async with SessionFactory() as session:
        users = await session.scalar(select(func.count()).select_from(User))
        orders = await session.scalar(select(func.count()).select_from(AdOrder))
        active = await session.scalar(
            select(func.count()).select_from(AdOrder).where(AdOrder.status == OrderStatus.ACTIVE.value)
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
    )


@router.callback_query(F.data == "adminv3:prices")
async def admin_prices(callback: CallbackQuery, state: FSMContext) -> None:
    if not await require_private_admin(callback):
        return
    await state.clear()
    async with SessionFactory() as session:
        prices = (
            await session.scalars(select(UserPrice).order_by(UserPrice.updated_at.desc()).limit(20))
        ).all()
    lines = ["<b><u>💰 ПЕРСОНАЛЬНЫЕ ЦЕНЫ</u></b>"]
    for row in prices:
        lines.append(
            f"\n<code>{row.user_id}</code> · {row.tariff_code}/{row.duration_code} — <b>{row.price_rub} ₽</b>"
        )
    if not prices:
        lines.append("\nПерсональных цен пока нет.")
    await callback.answer()
    await callback.bot.send_message(
        callback.from_user.id,
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="➕ Назначить цену",
                        callback_data="pricev3:add",
                        style="success",
                    )
                ],
                [InlineKeyboardButton(text="⬅️ Админ-панель", callback_data="profile:admin")],
            ]
        ),
    )


@router.callback_query(F.data == "pricev3:add")
async def price_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await require_private_admin(callback):
        return
    await state.set_state(AdminFlow.choosing_price_user)
    await callback.answer()
    await callback.bot.send_message(
        callback.from_user.id,
        "<b>Шаг 1/4 · Клиент</b>\n\nОтправьте только Telegram ID или @username клиента.",
    )


@router.callback_query(F.data.startswith("pricev3:user:"))
async def price_prefill_user(callback: CallbackQuery, state: FSMContext) -> None:
    if not await require_private_admin(callback):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    await state.update_data(price_user_id=user_id)
    await state.set_state(AdminFlow.choosing_price_tariff)
    await callback.answer()
    await callback.bot.send_message(
        callback.from_user.id,
        "<b>Шаг 2/4 · Тариф</b>",
        reply_markup=price_tariff_keyboard(),
    )


@router.message(AdminFlow.choosing_price_user, F.text)
async def price_choose_user(message: Message, state: FSMContext) -> None:
    if not message.from_user or not await admin_allowed(message.from_user.id):
        return
    raw = message.text.strip()
    async with SessionFactory() as session:
        if raw.startswith("@"):
            user = await session.scalar(
                select(User).where(func.lower(User.username) == raw[1:].lower())
            )
        else:
            try:
                user = await session.get(User, int(raw))
            except ValueError:
                user = None
    if not user:
        await message.answer("Клиент не найден. Он должен хотя бы один раз открыть бота.")
        return
    await state.update_data(price_user_id=user.id)
    await state.set_state(AdminFlow.choosing_price_tariff)
    await message.answer("<b>Шаг 2/4 · Выберите тариф</b>", reply_markup=price_tariff_keyboard())


@router.callback_query(AdminFlow.choosing_price_tariff, F.data.startswith("pricev3:tariff:"))
async def price_choose_tariff(callback: CallbackQuery, state: FSMContext) -> None:
    tariff = callback.data.rsplit(":", 1)[1]
    await state.update_data(price_tariff=tariff)
    await state.set_state(AdminFlow.choosing_price_duration)
    await callback.answer()
    await callback.bot.send_message(
        callback.from_user.id,
        "<b>Шаг 3/4 · Выберите срок</b>",
        reply_markup=price_duration_keyboard(),
    )


@router.callback_query(AdminFlow.choosing_price_duration, F.data.startswith("pricev3:duration:"))
async def price_choose_duration(callback: CallbackQuery, state: FSMContext) -> None:
    duration = callback.data.rsplit(":", 1)[1]
    await state.update_data(price_duration=duration)
    await state.set_state(AdminFlow.entering_price_amount)
    await callback.answer()
    await callback.bot.send_message(
        callback.from_user.id,
        "<b>Шаг 4/4 · Цена</b>\n\nОтправьте только сумму в рублях, например: <code>1200</code>.",
    )


@router.message(AdminFlow.entering_price_amount, F.text)
async def price_enter_amount(message: Message, state: FSMContext) -> None:
    try:
        amount = int(message.text.strip())
        if amount < 0:
            raise ValueError
    except ValueError:
        await message.answer("Отправьте только целую сумму, например: <code>1200</code>.")
        return
    await state.update_data(price_amount=amount)
    await state.set_state(AdminFlow.choosing_price_discount)
    await message.answer(
        "Какую скидку показать покупателю?",
        reply_markup=price_discount_keyboard(),
    )


@router.callback_query(AdminFlow.choosing_price_discount, F.data.startswith("pricev3:discount:"))
async def price_save(callback: CallbackQuery, state: FSMContext) -> None:
    if not await require_private_admin(callback):
        return
    discount = int(callback.data.rsplit(":", 1)[1])
    data = await state.get_data()
    now = datetime.now(timezone.utc)
    async with SessionFactory() as session:
        row = await session.scalar(
            select(UserPrice).where(
                UserPrice.user_id == data["price_user_id"],
                UserPrice.tariff_code == data["price_tariff"],
                UserPrice.duration_code == data["price_duration"],
            )
        )
        if row is None:
            row = UserPrice(
                user_id=data["price_user_id"],
                tariff_code=data["price_tariff"],
                duration_code=data["price_duration"],
                price_rub=data["price_amount"],
                announced_discount_percent=discount or None,
                updated_by=callback.from_user.id,
                updated_at=now,
            )
            session.add(row)
        else:
            row.price_rub = data["price_amount"]
            row.announced_discount_percent = discount or None
            row.updated_by = callback.from_user.id
            row.updated_at = now
        await session.commit()
    await state.clear()
    await callback.answer("Цена сохранена", show_alert=True)
    await callback.bot.send_message(
        data["price_user_id"],
        f"<b>🎁 Для вас обновлена цена</b>\n\n"
        f"{data['price_tariff']}/{data['price_duration']}: <b>{data['price_amount']} ₽</b>"
        + (f"\nСкидка: <b>{discount}%</b>" if discount else ""),
    )


@router.callback_query(F.data == "adminv3:settings")
async def admin_settings(callback: CallbackQuery) -> None:
    if not await require_private_admin(callback):
        return
    async with SessionFactory() as session:
        staff_chat_id = await get_staff_chat_id(session)
    await callback.answer()
    await callback.bot.send_message(
        callback.from_user.id,
        "<b><u>⚙️ НАСТРОЙКИ</u></b>\n\n"
        f"Группа стафа: <code>{staff_chat_id}</code>\n"
        "Проверка покажет точную ошибку Telegram и не требует доступа к серверу.",
        reply_markup=settings_keyboard(),
    )


@router.callback_query(F.data == "settingsv3:test_staff")
async def settings_test_staff(callback: CallbackQuery) -> None:
    if not await require_private_admin(callback):
        return
    await callback.answer("Проверяю…")
    ok, detail, chat_id = await test_staff_chat(callback.bot)
    await callback.bot.send_message(
        callback.from_user.id,
        ("<b>✅ Группа стафа работает</b>" if ok else "<b>❌ Группа стафа недоступна</b>")
        + f"\n\nID: <code>{chat_id}</code>\nРезультат: <code>{escape(detail)}</code>",
    )


@router.callback_query(F.data == "settingsv3:change_staff")
async def settings_change_staff(callback: CallbackQuery, state: FSMContext) -> None:
    if callback.from_user.id != settings.owner_id:
        await callback.answer("Изменить ID может только владелец.", show_alert=True)
        return
    await state.set_state(AdminFlow.entering_staff_chat_id)
    await callback.answer()
    await callback.bot.send_message(
        callback.from_user.id,
        "Отправьте только числовой ID группы стафа, например <code>-1001234567890</code>.",
    )


@router.message(AdminFlow.entering_staff_chat_id, F.text)
async def settings_save_staff(message: Message, state: FSMContext) -> None:
    if not message.from_user or message.from_user.id != settings.owner_id:
        return
    try:
        chat_id = int(message.text.strip())
        if chat_id >= 0:
            raise ValueError
    except ValueError:
        await message.answer("Нужен отрицательный числовой ID группы.")
        return
    async with SessionFactory() as session:
        await set_setting(session, STAFF_CHAT_KEY, str(chat_id), message.from_user.id)
    await state.clear()
    ok, detail, _ = await test_staff_chat(message.bot)
    await message.answer(
        ("✅ Новый ID сохранён и проверен." if ok else "⚠️ ID сохранён, но Telegram пока не видит группу.")
        + f"\n<code>{escape(detail)}</code>"
    )
