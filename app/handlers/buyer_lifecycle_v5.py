from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select, update

from app.db.session import SessionFactory
from app.enums import OrderStatus, TariffCode
from app.keyboards_v3 import buyer_orders_keyboard
from app.models import AdOrder, MiddlePinCandidate
from app.services.lifecycle import complete_order
from app.services.order_cards import update_buyer_card
from app.services.telegram_ads import confirm_middle_pin
from app.services.ui_screen import render_user_screen

router = Router(name="buyer_lifecycle_v5")


async def edit_screen(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    await render_user_screen(
        callback.bot,
        callback.from_user.id,
        text,
        markup,
        source_message=callback.message,
        media_key="main",
    )


@router.callback_query(F.data == "profile:orders")
async def orders_list(callback: CallbackQuery) -> None:
    async with SessionFactory() as session:
        orders = (
            await session.scalars(
                select(AdOrder)
                .where(AdOrder.user_id == callback.from_user.id)
                .order_by(AdOrder.id.desc())
                .limit(30)
            )
        ).all()
    markup = (
        buyer_orders_keyboard(orders)
        if orders
        else InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")]
            ]
        )
    )
    await edit_screen(
        callback,
        "<b><u>📂 МОИ РЕКЛАМЫ</u></b>\n\n"
        + (
            "Выберите заказ. Все действия откроются в этом же сообщении."
            if orders
            else "У вас пока нет заказов."
        ),
        markup,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("buyerorder:view:"))
async def order_view(callback: CallbackQuery, bot: Bot) -> None:
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.scalar(
            select(AdOrder).where(
                AdOrder.id == order_id,
                AdOrder.user_id == callback.from_user.id,
            )
        )
        if not order:
            await callback.answer("Заказ не найден.", show_alert=True)
            return
        await update_buyer_card(session, bot, order, source_message=callback.message)
    await callback.answer("Открыто")


@router.callback_query(F.data.startswith("buyerorder:pin:"))
async def begin_middle_pin(callback: CallbackQuery, bot: Bot) -> None:
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.scalar(
            select(AdOrder).where(
                AdOrder.id == order_id,
                AdOrder.user_id == callback.from_user.id,
            )
        )
        if not order or order.status != OrderStatus.ACTIVE.value:
            await callback.answer("Реклама не активна.", show_alert=True)
            return
        if order.tariff_code != TariffCode.MIDDLE.value:
            await callback.answer("Закреп доступен только для Middle.", show_alert=True)
            return
        if order.pinned_message_id and order.pin_changes_used >= 2:
            await callback.answer("Две замены уже использованы.", show_alert=True)
            return
        await session.execute(
            update(AdOrder)
            .where(
                AdOrder.user_id == callback.from_user.id,
                AdOrder.tariff_code == TariffCode.MIDDLE.value,
                AdOrder.status == OrderStatus.ACTIVE.value,
                AdOrder.id != order.id,
            )
            .values(awaiting_middle_pin=False)
        )
        candidate = await session.get(MiddlePinCandidate, order.id)
        if candidate:
            await session.delete(candidate)
        order.awaiting_middle_pin = True
        order.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await update_buyer_card(session, bot, order, source_message=callback.message)
    await callback.answer(
        "Отправьте рекламный пост в барахолку. Короткие реплики бот проигнорирует.",
        show_alert=True,
    )


@router.callback_query(F.data.startswith("middlepin:confirm:"))
async def confirm_candidate(callback: CallbackQuery, bot: Bot) -> None:
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.scalar(
            select(AdOrder).where(
                AdOrder.id == order_id,
                AdOrder.user_id == callback.from_user.id,
                AdOrder.status == OrderStatus.ACTIVE.value,
            ).with_for_update()
        )
        candidate = await session.get(MiddlePinCandidate, order_id)
        if not order or not candidate:
            await callback.answer("Пост для закрепа не найден.", show_alert=True)
            return
        await confirm_middle_pin(session, bot, order, candidate)
        await update_buyer_card(session, bot, order, source_message=callback.message)
    await callback.answer("✅ Новый пост закреплён, старый снят", show_alert=True)


@router.callback_query(F.data.startswith("middlepin:reject:"))
async def reject_candidate(callback: CallbackQuery, bot: Bot) -> None:
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.scalar(
            select(AdOrder).where(
                AdOrder.id == order_id,
                AdOrder.user_id == callback.from_user.id,
            )
        )
        candidate = await session.get(MiddlePinCandidate, order_id)
        if candidate:
            await session.delete(candidate)
        if order:
            order.awaiting_middle_pin = True
            order.updated_at = datetime.now(timezone.utc)
            await session.commit()
            await update_buyer_card(session, bot, order, source_message=callback.message)
    await callback.answer("Этот пост пропущен. Жду следующий рекламный пост.", show_alert=True)


@router.callback_query(F.data.startswith("buyerorder:pin_cancel:"))
async def cancel_pin_wait(callback: CallbackQuery, bot: Bot) -> None:
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.scalar(
            select(AdOrder).where(
                AdOrder.id == order_id,
                AdOrder.user_id == callback.from_user.id,
            )
        )
        if not order:
            await callback.answer("Заказ не найден.", show_alert=True)
            return
        candidate = await session.get(MiddlePinCandidate, order.id)
        if candidate:
            await session.delete(candidate)
        order.awaiting_middle_pin = False
        order.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await update_buyer_card(session, bot, order, source_message=callback.message)
    await callback.answer("Ожидание нового поста отменено", show_alert=True)


@router.callback_query(F.data.startswith("buyerorder:stop_confirm:"))
async def stop_confirm(callback: CallbackQuery) -> None:
    order_id = int(callback.data.rsplit(":", 1)[1])
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛑 Да, завершить рекламу",
                    callback_data=f"buyerorder:stop:{order_id}",
                    style="danger",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Нет, вернуться",
                    callback_data=f"buyerorder:view:{order_id}",
                )
            ],
        ]
    )
    await edit_screen(
        callback,
        f"<b>Завершить рекламу №{order_id}?</b>\n\n"
        "Закреп снимется, автопубликации остановятся, префикс будет удалён или пересчитан.",
        markup,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("buyerorder:stop:"))
async def stop_order(callback: CallbackQuery, bot: Bot) -> None:
    order_id = int(callback.data.rsplit(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.scalar(
            select(AdOrder).where(
                AdOrder.id == order_id,
                AdOrder.user_id == callback.from_user.id,
            ).with_for_update()
        )
        if not order:
            await callback.answer("Заказ не найден.", show_alert=True)
            return
        if order.status == OrderStatus.ACTIVE.value:
            await complete_order(session, bot, order, cancelled=True)
        else:
            await update_buyer_card(session, bot, order, source_message=callback.message)
    await callback.answer("Состояние заказа обновлено", show_alert=True)
