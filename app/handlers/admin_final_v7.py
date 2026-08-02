from __future__ import annotations

from datetime import datetime, timezone
from html import escape

from aiogram import F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select

from app.config import get_settings
from app.db.session import SessionFactory
from app.enums import OrderStatus
from app.keyboards import DURATION_NAMES, TARIFF_NAMES
from app.models import AdOrder, Admin, Payment, TariffPrice, User, UserPrice
from app.services.app_settings import (
    BAZAAR_CHAT_KEY,
    BAZAAR_URL_KEY,
    STAFF_CHAT_KEY,
    STATS_RESET_AT_KEY,
    get_bazaar_chat_id,
    get_bazaar_url,
    get_staff_chat_id,
    get_stats_reset_at,
    set_setting,
)
from app.services.ui_screen import delete_user_input, render_user_screen, send_ephemeral_notice
from app.services.users import is_admin
from app.states import AdminFlow

router = Router(name="admin_final_v7")
settings = get_settings()


async def allowed_callback(callback: CallbackQuery, *, owner_only: bool = False) -> bool:
    if callback.message and callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer("Раздел доступен только в личке бота.", show_alert=True)
        return False
    if owner_only and callback.from_user.id != settings.owner_id:
        await callback.answer("Это действие доступно только владельцу.", show_alert=True)
        return False
    async with SessionFactory() as session:
        allowed = await is_admin(session, callback.from_user.id)
    if not allowed:
        await callback.answer("Доступ запрещён.", show_alert=True)
    return allowed


async def allowed_message(message: Message, *, owner_only: bool = False) -> bool:
    if not message.from_user or message.chat.type != ChatType.PRIVATE:
        return False
    if owner_only and message.from_user.id != settings.owner_id:
        return False
    async with SessionFactory() as session:
        return await is_admin(session, message.from_user.id)


async def screen(
    callback: CallbackQuery,
    text: str,
    markup: InlineKeyboardMarkup,
    *,
    text_only: bool = False,
) -> None:
    await render_user_screen(
        callback.bot,
        callback.from_user.id,
        text,
        markup,
        source_message=callback.message,
        media_key="admin",
        text_only=text_only,
    )


async def message_screen(
    message: Message,
    text: str,
    markup: InlineKeyboardMarkup,
) -> None:
    await render_user_screen(
        message.bot,
        message.from_user.id,
        text,
        markup,
        media_key="admin",
    )
    await delete_user_input(message)


def back_admin() -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(text="⬅️ Админ-панель", callback_data="profile:admin"),
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home"),
    ]


def admins_keyboard(items: list[Admin]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="➕ Добавить администратора",
                callback_data="adminv7:add",
                style="success",
            ),
            InlineKeyboardButton(
                text="➖ Удалить администратора",
                callback_data="adminv7:remove_list",
                style="danger",
            ),
        ]
    ]
    if items:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔄 Обновить список",
                    callback_data="adminv3:admins",
                    style="primary",
                )
            ]
        )
    rows.append(back_admin())
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def render_admins(callback: CallbackQuery) -> None:
    async with SessionFactory() as session:
        admins = (await session.scalars(select(Admin).order_by(Admin.added_at))).all()
        users = {
            item.user_id: await session.get(User, item.user_id)
            for item in admins
        }
    lines = [
        "<b><u>👮 АДМИНИСТРАТОРЫ</u></b>",
        "",
        f"👑 Владелец: <code>{settings.owner_id}</code>",
    ]
    for item in admins:
        if item.user_id == settings.owner_id:
            continue
        user = users.get(item.user_id)
        name = escape(user.first_name) if user else "профиль не найден"
        lines.append(f"👤 {name} · <code>{item.user_id}</code> · {escape(item.role)}")
    if len(lines) == 3:
        lines.append("Других администраторов пока нет.")
    await screen(callback, "\n".join(lines), admins_keyboard(admins))


@router.callback_query(F.data == "adminv3:admins")
async def admins_page(callback: CallbackQuery, state: FSMContext) -> None:
    if not await allowed_callback(callback):
        return
    await state.clear()
    await render_admins(callback)
    await callback.answer()


@router.callback_query(F.data.in_({"admin:add", "adminv7:add"}))
async def add_admin_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await allowed_callback(callback, owner_only=True):
        return
    await state.set_state(AdminFlow.adding_admin)
    await screen(
        callback,
        "<b>➕ Добавление администратора</b>\n\n"
        "Отправьте Telegram ID пользователя. Пользователь должен хотя бы один раз открыть бота.",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ К администраторам", callback_data="adminv3:admins")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
            ]
        ),
    )
    await callback.answer()


@router.message(AdminFlow.adding_admin, F.text)
async def add_admin_save(message: Message, state: FSMContext) -> None:
    if not await allowed_message(message, owner_only=True):
        return
    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message_screen(
            message,
            "<b>❌ Нужен числовой Telegram ID</b>\n\nПопробуйте ещё раз.",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Отмена", callback_data="adminv3:admins")],
                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
                ]
            ),
        )
        return

    async with SessionFactory() as session:
        user = await session.get(User, user_id)
        existing = await session.get(Admin, user_id)
        if not user:
            result = "Пользователь не найден. Сначала он должен открыть бота."
        elif user_id == settings.owner_id:
            result = "Владелец уже имеет полный доступ."
        elif existing:
            result = "Пользователь уже является администратором."
        else:
            session.add(
                Admin(
                    user_id=user_id,
                    role="admin",
                    added_by=message.from_user.id,
                    added_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()
            result = f"✅ Администратор <code>{user_id}</code> добавлен."
    await state.clear()
    await message_screen(
        message,
        f"<b>{result}</b>",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Готово", callback_data="adminv3:admins", style="success")],
                [InlineKeyboardButton(text="➕ Добавить ещё", callback_data="adminv7:add")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
            ]
        ),
    )


@router.callback_query(F.data.in_({"admin:remove", "adminv7:remove_list"}))
async def remove_admin_list(callback: CallbackQuery) -> None:
    if not await allowed_callback(callback, owner_only=True):
        return
    async with SessionFactory() as session:
        admins = (
            await session.scalars(
                select(Admin)
                .where(Admin.user_id != settings.owner_id)
                .order_by(Admin.added_at)
            )
        ).all()
    rows = [
        [
            InlineKeyboardButton(
                text=f"➖ {item.user_id}",
                callback_data=f"adminv7:remove_confirm:{item.user_id}",
                style="danger",
            )
        ]
        for item in admins
    ]
    rows.append([InlineKeyboardButton(text="⬅️ К администраторам", callback_data="adminv3:admins")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")])
    await screen(
        callback,
        "<b>➖ Удаление администратора</b>\n\n"
        + ("Выберите пользователя." if admins else "Удалять некого."),
        InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adminv7:remove_confirm:"))
async def remove_admin_confirm(callback: CallbackQuery) -> None:
    if not await allowed_callback(callback, owner_only=True):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    await screen(
        callback,
        f"<b>Удалить администратора <code>{user_id}</code>?</b>",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="➖ Да, удалить",
                        callback_data=f"adminv7:remove_do:{user_id}",
                        style="danger",
                    )
                ],
                [InlineKeyboardButton(text="⬅️ Нет", callback_data="adminv7:remove_list")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adminv7:remove_do:"))
async def remove_admin_do(callback: CallbackQuery) -> None:
    if not await allowed_callback(callback, owner_only=True):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        row = await session.get(Admin, user_id)
        if row:
            await session.delete(row)
            await session.commit()
    await screen(
        callback,
        f"<b>✅ Администратор <code>{user_id}</code> удалён</b>",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Готово", callback_data="adminv3:admins", style="success")],
                [InlineKeyboardButton(text="➖ Удалить ещё", callback_data="adminv7:remove_list")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
            ]
        ),
    )
    await callback.answer("Удалён")


def settings_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🧪 Проверить стаф", callback_data="settingsv3:test_staff"),
                InlineKeyboardButton(text="✏️ ID стафа", callback_data="settingsv3:change_staff"),
            ],
            [
                InlineKeyboardButton(text="🧪 Проверить группу", callback_data="settingsv7:test_bazaar"),
                InlineKeyboardButton(text="✏️ ID группы", callback_data="settingsv7:change_bazaar"),
            ],
            [
                InlineKeyboardButton(
                    text="🔗 Изменить ссылку группы",
                    callback_data="settingsv7:change_bazaar_url",
                    style="primary",
                )
            ],
            [back_admin()],
        ]
    )


async def settings_text() -> str:
    async with SessionFactory() as session:
        staff_id = await get_staff_chat_id(session)
        bazaar_id = await get_bazaar_chat_id(session)
        bazaar_url = await get_bazaar_url(session)
    return (
        "<b><u>⚙️ НАСТРОЙКИ ГРУПП</u></b>\n\n"
        f"├ Рабочая группа: <code>{bazaar_id}</code>\n"
        f"├ Ссылка: <code>{escape(bazaar_url)}</code>\n"
        f"└ Группа стафа: <code>{staff_id}</code>\n\n"
        "<i>Смена рабочей группы блокируется, пока есть активная реклама: иначе старые закрепы останутся в прежней группе.</i>"
    )


@router.callback_query(F.data == "adminv3:settings")
async def settings_page(callback: CallbackQuery, state: FSMContext) -> None:
    if not await allowed_callback(callback):
        return
    await state.clear()
    await screen(callback, await settings_text(), settings_markup())
    await callback.answer()


async def group_diagnostic(bot, chat_id: int, *, need_pin: bool) -> tuple[bool, str]:
    try:
        chat = await bot.get_chat(chat_id)
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id, me.id)
        status = getattr(member.status, "value", member.status)
        is_admin_status = status in {
            ChatMemberStatus.ADMINISTRATOR.value,
            ChatMemberStatus.CREATOR.value,
        }
        can_pin = bool(getattr(member, "can_pin_messages", False)) or status == ChatMemberStatus.CREATOR.value
        if not is_admin_status:
            return False, f"Бот не администратор. Статус: {status}"
        if need_pin and not can_pin:
            return False, "У бота нет права закреплять сообщения."
        return True, f"{chat.title or chat_id}: доступ подтверждён"
    except Exception as error:
        return False, f"{type(error).__name__}: {error}"


@router.callback_query(F.data == "settingsv3:test_staff")
async def test_staff(callback: CallbackQuery) -> None:
    if not await allowed_callback(callback):
        return
    async with SessionFactory() as session:
        chat_id = await get_staff_chat_id(session)
    ok, detail = await group_diagnostic(callback.bot, chat_id, need_pin=False)
    await screen(
        callback,
        await settings_text()
        + f"\n\n<b>{'✅ Стаф работает' if ok else '❌ Ошибка стафа'}</b>\n<code>{escape(detail)}</code>",
        settings_markup(),
    )
    await callback.answer("Проверено")


@router.callback_query(F.data == "settingsv7:test_bazaar")
async def test_bazaar(callback: CallbackQuery) -> None:
    if not await allowed_callback(callback):
        return
    async with SessionFactory() as session:
        chat_id = await get_bazaar_chat_id(session)
    ok, detail = await group_diagnostic(callback.bot, chat_id, need_pin=True)
    await screen(
        callback,
        await settings_text()
        + f"\n\n<b>{'✅ Рабочая группа готова' if ok else '❌ Ошибка рабочей группы'}</b>\n<code>{escape(detail)}</code>",
        settings_markup(),
    )
    await callback.answer("Проверено")


async def start_setting_input(
    callback: CallbackQuery,
    state: FSMContext,
    target_state,
    title: str,
    hint: str,
) -> None:
    if not await allowed_callback(callback, owner_only=True):
        return
    await state.set_state(target_state)
    await screen(
        callback,
        f"<b>{title}</b>\n\n{hint}",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ К настройкам", callback_data="adminv3:settings")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "settingsv3:change_staff")
async def change_staff(callback: CallbackQuery, state: FSMContext) -> None:
    await start_setting_input(
        callback,
        state,
        AdminFlow.entering_staff_chat_id,
        "✏️ ID группы стафа",
        "Отправьте отрицательный ID, например <code>-1001234567890</code>.",
    )


@router.callback_query(F.data == "settingsv7:change_bazaar")
async def change_bazaar(callback: CallbackQuery, state: FSMContext) -> None:
    await start_setting_input(
        callback,
        state,
        AdminFlow.entering_bazaar_chat_id,
        "✏️ ID рабочей группы",
        "Отправьте отрицательный ID группы. Бот проверит права перед сохранением.",
    )


@router.callback_query(F.data == "settingsv7:change_bazaar_url")
async def change_bazaar_url(callback: CallbackQuery, state: FSMContext) -> None:
    await start_setting_input(
        callback,
        state,
        AdminFlow.entering_bazaar_url,
        "🔗 Ссылка рабочей группы",
        "Отправьте полную ссылку, например <code>https://t.me/example</code>.",
    )


async def parse_negative_chat_id(message: Message) -> int | None:
    try:
        value = int(message.text.strip())
        return value if value < 0 else None
    except (TypeError, ValueError):
        return None


@router.message(AdminFlow.entering_staff_chat_id, F.text)
async def save_staff(message: Message, state: FSMContext) -> None:
    if not await allowed_message(message, owner_only=True):
        return
    chat_id = await parse_negative_chat_id(message)
    if chat_id is None:
        await message_screen(
            message,
            "<b>❌ Нужен отрицательный числовой ID</b>",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Отмена", callback_data="adminv3:settings")],
                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
                ]
            ),
        )
        return
    ok, detail = await group_diagnostic(message.bot, chat_id, need_pin=False)
    if ok:
        async with SessionFactory() as session:
            await set_setting(session, STAFF_CHAT_KEY, str(chat_id), message.from_user.id)
        await state.clear()
    await message_screen(
        message,
        f"<b>{'✅ ID стафа сохранён' if ok else '❌ ID не сохранён'}</b>\n\n<code>{escape(detail)}</code>",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Готово", callback_data="adminv3:settings", style="success")],
                [InlineKeyboardButton(text="🔄 Попробовать другой ID", callback_data="settingsv3:change_staff")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
            ]
        ),
    )


@router.message(AdminFlow.entering_bazaar_chat_id, F.text)
async def save_bazaar(message: Message, state: FSMContext) -> None:
    if not await allowed_message(message, owner_only=True):
        return
    chat_id = await parse_negative_chat_id(message)
    if chat_id is None:
        await message_screen(
            message,
            "<b>❌ Нужен отрицательный числовой ID</b>",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Отмена", callback_data="adminv3:settings")],
                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
                ]
            ),
        )
        return

    async with SessionFactory() as session:
        current = await get_bazaar_chat_id(session)
        active = await session.scalar(
            select(func.count()).select_from(AdOrder).where(
                AdOrder.status == OrderStatus.ACTIVE.value
            )
        )
    if chat_id != current and int(active or 0) > 0:
        await state.clear()
        await message_screen(
            message,
            "<b>❌ Рабочая группа не изменена</b>\n\n"
            "Сначала завершите активные рекламы. Иначе Telegram оставит закрепы и префиксы в старой группе.",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🚀 Активная реклама", callback_data="adminv3:active")],
                    [InlineKeyboardButton(text="⬅️ К настройкам", callback_data="adminv3:settings")],
                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
                ]
            ),
        )
        return

    ok, detail = await group_diagnostic(message.bot, chat_id, need_pin=True)
    if ok:
        async with SessionFactory() as session:
            await set_setting(session, BAZAAR_CHAT_KEY, str(chat_id), message.from_user.id)
        await state.clear()
    await message_screen(
        message,
        f"<b>{'✅ Рабочая группа сохранена' if ok else '❌ Группа не сохранена'}</b>\n\n<code>{escape(detail)}</code>",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Готово", callback_data="adminv3:settings", style="success")],
                [InlineKeyboardButton(text="🔄 Другой ID", callback_data="settingsv7:change_bazaar")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
            ]
        ),
    )


@router.message(AdminFlow.entering_bazaar_url, F.text)
async def save_bazaar_url(message: Message, state: FSMContext) -> None:
    if not await allowed_message(message, owner_only=True):
        return
    value = message.text.strip()
    valid = value.startswith(("https://t.me/", "http://t.me/", "https://telegram.me/"))
    if valid:
        async with SessionFactory() as session:
            await set_setting(session, BAZAAR_URL_KEY, value, message.from_user.id)
        await state.clear()
    await message_screen(
        message,
        (
            "<b>✅ Ссылка рабочей группы сохранена</b>"
            if valid
            else "<b>❌ Ссылка не распознана</b>\n\nИспользуйте полную ссылку вида <code>https://t.me/example</code>."
        ),
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Готово", callback_data="adminv3:settings", style="success")],
                [InlineKeyboardButton(text="🔄 Другая ссылка", callback_data="settingsv7:change_bazaar_url")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
            ]
        ),
    )


def stats_markup(owner: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="🔄 Обновить", callback_data="adminv3:stats", style="primary")]
    ]
    if owner:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🧹 Очистить статистику",
                    callback_data="statsv7:clear_confirm",
                    style="danger",
                )
            ]
        )
    rows.append(back_admin())
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "adminv3:stats")
async def stats_page(callback: CallbackQuery) -> None:
    if not await allowed_callback(callback):
        return
    async with SessionFactory() as session:
        reset_at = await get_stats_reset_at(session)
        user_query = select(func.count()).select_from(User)
        order_query = select(func.count()).select_from(AdOrder)
        payment_query = select(func.coalesce(func.sum(Payment.amount_rub), 0))
        if reset_at:
            user_query = user_query.where(User.first_seen_at >= reset_at)
            order_query = order_query.where(AdOrder.created_at >= reset_at)
            payment_query = payment_query.where(
                func.coalesce(Payment.paid_at, Payment.created_at) >= reset_at
            )
        users = await session.scalar(user_query)
        orders = await session.scalar(order_query)
        paid = await session.scalar(payment_query)
        active = await session.scalar(
            select(func.count()).select_from(AdOrder).where(
                AdOrder.status == OrderStatus.ACTIVE.value
            )
        )
        booked = await session.scalar(
            select(func.count()).select_from(AdOrder).where(
                AdOrder.status == OrderStatus.BOOKED.value
            )
        )
    period = reset_at.strftime("%d.%m.%Y %H:%M UTC") if reset_at else "за всё время"
    await screen(
        callback,
        "<b><u>📊 СТАТИСТИКА</u></b>\n\n"
        f"Период: <b>{period}</b>\n\n"
        f"├ Новых пользователей: <b>{users or 0}</b>\n"
        f"├ Новых заказов: <b>{orders or 0}</b>\n"
        f"├ Активных сейчас: <b>{active or 0}</b>\n"
        f"├ В очереди сейчас: <b>{booked or 0}</b>\n"
        f"└ Оплачено за период: <b>{int(paid or 0)} ₽</b>",
        stats_markup(callback.from_user.id == settings.owner_id),
    )
    await callback.answer()


@router.callback_query(F.data == "statsv7:clear_confirm")
async def stats_clear_confirm(callback: CallbackQuery) -> None:
    if not await allowed_callback(callback, owner_only=True):
        return
    await screen(
        callback,
        "<b>🧹 Обнулить отображаемую статистику?</b>\n\n"
        "Заказы, клиенты и платежи не удалятся. Бот просто начнёт новый статистический период с текущего момента.",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🧹 Да, начать новый период",
                        callback_data="statsv7:clear_do",
                        style="danger",
                    )
                ],
                [InlineKeyboardButton(text="⬅️ Нет", callback_data="adminv3:stats")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "statsv7:clear_do")
async def stats_clear_do(callback: CallbackQuery) -> None:
    if not await allowed_callback(callback, owner_only=True):
        return
    async with SessionFactory() as session:
        await set_setting(
            session,
            STATS_RESET_AT_KEY,
            datetime.now(timezone.utc).isoformat(),
            callback.from_user.id,
        )
    await callback.answer("Статистика обнулена", show_alert=True)
    await stats_page(callback)


def prices_nav(extra: list[list[InlineKeyboardButton]] | None = None) -> InlineKeyboardMarkup:
    rows = list(extra or [])
    rows.append(
        [
            InlineKeyboardButton(text="⬅️ Админ-панель", callback_data="profile:admin"),
            InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "adminv3:prices")
async def prices_page(callback: CallbackQuery, state: FSMContext) -> None:
    if not await allowed_callback(callback):
        return
    await state.clear()
    async with SessionFactory() as session:
        items = (
            await session.scalars(
                select(UserPrice).order_by(UserPrice.updated_at.desc()).limit(12)
            )
        ).all()
    lines = ["<b><u>💰 ПЕРСОНАЛЬНЫЕ ЦЕНЫ</u></b>"]
    for item in items:
        lines.append(
            f"<code>{item.user_id}</code> · "
            f"{TARIFF_NAMES.get(item.tariff_code, item.tariff_code)} · "
            f"{DURATION_NAMES.get(item.duration_code, item.duration_code)} — "
            f"<b>{item.price_rub} ₽</b>"
            + (f" · скидка {item.announced_discount_percent}%" if item.announced_discount_percent else "")
        )
    if not items:
        lines.append("Персональных цен пока нет.")
    await screen(
        callback,
        "\n\n".join(lines),
        prices_nav(
            [
                [
                    InlineKeyboardButton(
                        text="➕ Назначить цену",
                        callback_data="pricev3:add",
                        style="success",
                    ),
                    InlineKeyboardButton(
                        text="🔄 Обновить",
                        callback_data="adminv3:prices",
                        style="primary",
                    ),
                ]
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "pricev3:add")
async def price_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not await allowed_callback(callback):
        return
    await state.clear()
    await state.set_state(AdminFlow.choosing_price_user)
    await screen(
        callback,
        "<b>Шаг 1/5 · Клиент</b>\n\nОтправьте Telegram ID или @username клиента.",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ К ценам", callback_data="adminv3:prices")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pricev3:user:"))
async def price_user_prefill(callback: CallbackQuery, state: FSMContext) -> None:
    if not await allowed_callback(callback):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    await state.clear()
    await state.update_data(price_user_id=user_id)
    await state.set_state(AdminFlow.choosing_price_tariff)
    await render_price_tariffs(callback)
    await callback.answer()


def tariff_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Standard 🎪", callback_data="pricev3:tariff:standard"),
                InlineKeyboardButton(text="Middle 🎭", callback_data="pricev3:tariff:middle"),
                InlineKeyboardButton(text="Best 🔥", callback_data="pricev3:tariff:best"),
            ],
            [InlineKeyboardButton(text="⬅️ Другой клиент", callback_data="pricev3:add")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
        ]
    )


async def render_price_tariffs(callback: CallbackQuery) -> None:
    await screen(callback, "<b>Шаг 2/5 · Выберите тариф</b>", tariff_markup())


@router.message(AdminFlow.choosing_price_user, F.text)
async def price_choose_user(message: Message, state: FSMContext) -> None:
    if not await allowed_message(message):
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
    await delete_user_input(message)
    if not user:
        await render_user_screen(
            message.bot,
            message.from_user.id,
            "<b>❌ Клиент не найден</b>\n\nОн должен хотя бы один раз открыть бота.",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="pricev3:add")],
                    [InlineKeyboardButton(text="⬅️ К ценам", callback_data="adminv3:prices")],
                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
                ]
            ),
            media_key="admin",
        )
        return
    await state.update_data(price_user_id=user.id)
    await state.set_state(AdminFlow.choosing_price_tariff)
    await render_user_screen(
        message.bot,
        message.from_user.id,
        "<b>Шаг 2/5 · Выберите тариф</b>",
        tariff_markup(),
        media_key="admin",
    )


@router.callback_query(AdminFlow.choosing_price_tariff, F.data.startswith("pricev3:tariff:"))
async def price_choose_tariff(callback: CallbackQuery, state: FSMContext) -> None:
    tariff = callback.data.rsplit(":", 1)[1]
    await state.update_data(price_tariff=tariff)
    await state.set_state(AdminFlow.choosing_price_duration)
    await screen(
        callback,
        "<b>Шаг 3/5 · Выберите срок</b>",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="1 день", callback_data="pricev3:duration:day"),
                    InlineKeyboardButton(text="7 дней", callback_data="pricev3:duration:week"),
                    InlineKeyboardButton(text="30 дней", callback_data="pricev3:duration:month"),
                ],
                [InlineKeyboardButton(text="⬅️ К тарифам", callback_data="pricev7:back_tariff")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "pricev7:back_tariff")
async def price_back_tariff(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminFlow.choosing_price_tariff)
    await render_price_tariffs(callback)
    await callback.answer()


@router.callback_query(AdminFlow.choosing_price_duration, F.data.startswith("pricev3:duration:"))
async def price_choose_duration(callback: CallbackQuery, state: FSMContext) -> None:
    duration = callback.data.rsplit(":", 1)[1]
    await state.update_data(price_duration=duration)
    await state.set_state(AdminFlow.entering_price_amount)
    await screen(
        callback,
        "<b>Шаг 4/5 · Персональная цена</b>\n\n"
        "Отправьте целую сумму в рублях, например <code>1200</code>.",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ К срокам", callback_data="pricev7:back_duration")],
                [InlineKeyboardButton(text="⬅️ К тарифам", callback_data="pricev7:back_tariff")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "pricev7:back_duration")
async def price_back_duration(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("price_tariff"):
        await price_back_tariff(callback, state)
        return
    await state.set_state(AdminFlow.choosing_price_duration)
    await screen(
        callback,
        "<b>Шаг 3/5 · Выберите срок</b>",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="1 день", callback_data="pricev3:duration:day"),
                    InlineKeyboardButton(text="7 дней", callback_data="pricev3:duration:week"),
                    InlineKeyboardButton(text="30 дней", callback_data="pricev3:duration:month"),
                ],
                [InlineKeyboardButton(text="⬅️ К тарифам", callback_data="pricev7:back_tariff")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
            ]
        ),
    )
    await callback.answer()


@router.message(AdminFlow.entering_price_amount, F.text)
async def price_enter_amount(message: Message, state: FSMContext) -> None:
    if not await allowed_message(message):
        return
    try:
        amount = int(message.text.strip())
        if amount < 0:
            raise ValueError
    except ValueError:
        await message_screen(
            message,
            "<b>❌ Нужна целая сумма</b>\n\nНапример: <code>1200</code>.",
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Ввести снова", callback_data="pricev7:back_amount")],
                    [InlineKeyboardButton(text="⬅️ К срокам", callback_data="pricev7:back_duration")],
                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
                ]
            ),
        )
        return
    await state.update_data(price_amount=amount)
    await state.set_state(AdminFlow.choosing_price_discount)
    await message_screen(
        message,
        "<b>Шаг 5/5 · Скидка для покупателя</b>\n\n"
        "Выберите процент, который будет красиво показан покупателю.",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Без скидки", callback_data="pricev3:discount:0"),
                    InlineKeyboardButton(text="5%", callback_data="pricev3:discount:5"),
                    InlineKeyboardButton(text="10%", callback_data="pricev3:discount:10"),
                    InlineKeyboardButton(text="15%", callback_data="pricev3:discount:15"),
                ],
                [
                    InlineKeyboardButton(text="20%", callback_data="pricev3:discount:20"),
                    InlineKeyboardButton(text="25%", callback_data="pricev3:discount:25"),
                    InlineKeyboardButton(text="30%", callback_data="pricev3:discount:30"),
                ],
                [InlineKeyboardButton(text="⬅️ Изменить сумму", callback_data="pricev7:back_amount")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
            ]
        ),
    )


@router.callback_query(F.data == "pricev7:back_amount")
async def price_back_amount(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminFlow.entering_price_amount)
    await screen(
        callback,
        "<b>Шаг 4/5 · Персональная цена</b>\n\nОтправьте новую сумму в рублях.",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ К срокам", callback_data="pricev7:back_duration")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(AdminFlow.choosing_price_discount, F.data.startswith("pricev3:discount:"))
async def price_save(callback: CallbackQuery, state: FSMContext) -> None:
    if not await allowed_callback(callback):
        return
    discount = int(callback.data.rsplit(":", 1)[1])
    data = await state.get_data()
    required = {"price_user_id", "price_tariff", "price_duration", "price_amount"}
    if not required.issubset(data):
        await callback.answer("Оформление цены устарело. Начните заново.", show_alert=True)
        await state.clear()
        return

    now = datetime.now(timezone.utc)
    async with SessionFactory() as session:
        user = await session.get(User, data["price_user_id"])
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
    tariff_name = TARIFF_NAMES.get(data["price_tariff"], data["price_tariff"])
    duration_name = DURATION_NAMES.get(data["price_duration"], data["price_duration"])
    buyer_name = escape(user.first_name or str(user.id)) if user else str(data["price_user_id"])
    notification = (
        "<b>🎁 Вам назначена персональная цена</b>\n\n"
        f"├ Тариф: <b>{tariff_name}</b>\n"
        f"├ Срок: <b>{duration_name}</b>\n"
        f"├ Цена: <b>{data['price_amount']} ₽</b>\n"
        f"└ Скидка: <b>{discount}%</b>"
        if discount
        else
        "<b>🎁 Вам назначена персональная цена</b>\n\n"
        f"├ Тариф: <b>{tariff_name}</b>\n"
        f"├ Срок: <b>{duration_name}</b>\n"
        f"└ Цена: <b>{data['price_amount']} ₽</b>"
    )
    await send_ephemeral_notice(callback.bot, data["price_user_id"], notification, seconds=30)
    await screen(
        callback,
        "<b>✅ Персональная цена сохранена</b>\n\n"
        f"├ Клиент: <b>{buyer_name}</b> · <code>{data['price_user_id']}</code>\n"
        f"├ Тариф: <b>{tariff_name}</b>\n"
        f"├ Срок: <b>{duration_name}</b>\n"
        f"├ Цена: <b>{data['price_amount']} ₽</b>\n"
        f"└ Скидка: <b>{discount}%</b>",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Готово", callback_data="adminv3:prices", style="success")],
                [InlineKeyboardButton(text="➕ Назначить ещё", callback_data="pricev3:add")],
                [
                    InlineKeyboardButton(
                        text="👤 К карточке клиента",
                        callback_data=f"adminv3:client:{data['price_user_id']}",
                    )
                ],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
            ]
        ),
    )
    await callback.answer("Цена сохранена")
