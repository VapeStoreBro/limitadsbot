from html import escape

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import func, select

from app.config import get_settings
from app.db.session import SessionFactory
from app.enums import OrderStatus
from app.keyboards_v3 import client_actions_keyboard, home_keyboard
from app.models import AdOrder, User, UserBlock
from app.services.blocking import block_user, unblock_user
from app.services.ui_screen import render_user_screen, send_ephemeral_notice
from app.services.users import is_admin

router = Router(name="client_single_v6")
settings = get_settings()


async def allowed(callback: CallbackQuery) -> bool:
    if callback.message and callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer("Раздел доступен только в личке.", show_alert=True)
        return False
    async with SessionFactory() as session:
        value = await is_admin(session, callback.from_user.id)
    if not value:
        await callback.answer("Доступ запрещён.", show_alert=True)
    return value


async def admin_screen(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    await render_user_screen(
        callback.bot,
        callback.from_user.id,
        text,
        markup,
        source_message=callback.message,
        media_key="main",
    )


async def render_client(callback: CallbackQuery, user_id: int, saved: str | None = None) -> None:
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
    active_text = ", ".join(f"№{item.id} {item.tariff_code}" for item in active) or "нет"
    result = f"\n\n<b>✅ {escape(saved)}</b>" if saved else ""
    await admin_screen(
        callback,
        "<b><u>👤 КЛИЕНТ</u></b>\n\n"
        f"├ Имя: <a href=\"tg://user?id={user.id}\"><b>{escape(user.first_name or str(user.id))}</b></a>\n"
        f"├ ID: <code>{user.id}</code>\n"
        f"├ Username: @{escape(user.username) if user.username else 'нет'}\n"
        f"├ Телефон: <code>{escape(user.phone or 'не указан')}</code>\n"
        f"├ Заказов: <b>{orders_count or 0}</b>\n"
        f"├ Оплачено: <b>{int(paid or 0)} ₽</b>\n"
        f"├ Активные: <b>{escape(active_text)}</b>\n"
        f"└ Доступ: <b>{'🚫 заблокирован' if block else '✅ разрешён'}</b>"
        f"{result}",
        client_actions_keyboard(user.id, blocked=bool(block)),
    )


@router.callback_query(F.data.startswith("clientv4:block_confirm:"))
async def block_confirm(callback: CallbackQuery) -> None:
    if not await allowed(callback):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    if user_id == settings.owner_id:
        await callback.answer("Владельца блокировать нельзя.", show_alert=True)
        return
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚫 Заблокировать и остановить рекламу",
                    callback_data=f"clientv4:block:{user_id}",
                    style="danger",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Отмена",
                    callback_data=f"adminv3:client:{user_id}",
                )
            ],
        ]
    )
    await admin_screen(
        callback,
        "<b>⚠️ Заблокировать клиента?</b>\n\n"
        "Активные рекламы завершатся, закрепы снимутся, Best остановится, префикс обновится, незавершённые заявки отменятся.",
        markup,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("clientv4:block:"))
async def block(callback: CallbackQuery) -> None:
    if not await allowed(callback):
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
    await render_user_screen(
        callback.bot,
        user_id,
        "<b>🚫 Доступ к покупке рекламы ограничен</b>\n\n"
        "Активные размещения остановлены, закрепы и префикс сняты.",
        home_keyboard(),
        media_key="main",
    )
    await send_ephemeral_notice(
        callback.bot,
        user_id,
        "<b>🚫 Доступ ограничен администрацией</b>",
    )
    await render_client(callback, user_id, "Клиент заблокирован, активные размещения остановлены")
    await callback.answer("Клиент заблокирован", show_alert=True)


@router.callback_query(F.data.startswith("clientv4:unblock:"))
async def unblock(callback: CallbackQuery) -> None:
    if not await allowed(callback):
        return
    user_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        changed = await unblock_user(session, user_id)
    if changed:
        await render_user_screen(
            callback.bot,
            user_id,
            "<b>✅ Доступ к покупке рекламы восстановлен</b>\n\n"
            "Откройте главное меню для нового размещения.",
            home_keyboard(),
            media_key="main",
        )
        await send_ephemeral_notice(
            callback.bot,
            user_id,
            "<b>✅ Доступ восстановлен</b>",
        )
    await render_client(
        callback,
        user_id,
        "Доступ восстановлен" if changed else "Клиент не был заблокирован",
    )
    await callback.answer("Готово", show_alert=True)
