from __future__ import annotations

from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from app.db.session import SessionFactory
from app.enums import OrderStatus, TariffCode
from app.handlers.best_buttons_v3 import normalize_url
from app.models import AdOrder, OrderCard
from app.services.order_cards import BUYER_CARD, update_buyer_card, update_staff_card
from app.services.staff_delivery import current_staff_chat_id, send_ad_content_resilient
from app.services.telegram_ads import replace_best_publication
from app.states import BestEditFlow

router = Router(name="best_edit_v5")
TERMINAL = {
    OrderStatus.COMPLETED.value,
    OrderStatus.CANCELLED.value,
    OrderStatus.REJECTED.value,
}


def get_button(order: AdOrder, kind: str) -> dict[str, str] | None:
    return next((button for button in order.buttons or [] if button.get("kind") == kind), None)


def edit_menu_text(order: AdOrder, saved: str | None = None) -> str:
    contact = get_button(order, "contact")
    resource = get_button(order, "resource")
    lines = [
        f"<b><u>✏️ РЕДАКТИРОВАНИЕ BEST №{order.id}</u></b>",
        "",
        "Меняйте только нужную часть. После сохранения активный закреплённый пост обновится автоматически.",
        "",
        f"├ Текст: <b>{'✅ заполнен' if order.content_text else 'не заполнен'}</b>",
        f"├ Фото: <b>{len(order.media or [])}/8</b>",
        f"├ Контакт: <b>{'✅ добавлен' if contact else 'не добавлен'}</b>",
        f"└ Ресурс: <b>{'✅ добавлен' if resource else 'не добавлен'}</b>",
    ]
    if saved:
        lines.extend(["", f"<b>✅ {escape(saved)}</b>"])
    return "\n".join(lines)


def edit_menu_keyboard(order: AdOrder) -> InlineKeyboardMarkup:
    contact = get_button(order, "contact")
    resource = get_button(order, "resource")
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="✅ Текст" if order.content_text else "📝 Добавить текст",
                callback_data=f"bestedit:text:{order.id}",
                style="success" if order.content_text else "primary",
            ),
            InlineKeyboardButton(
                text=f"✅ Фото {len(order.media or [])}" if order.media else "➕ Фото",
                callback_data=f"bestedit:add_photo:{order.id}",
                style="success" if order.media else "primary",
            ),
        ],
        [
            InlineKeyboardButton(
                text="✅ Контакт" if contact else "➕ Контакт",
                callback_data=f"bestedit:contact:{order.id}",
                style="success" if contact else "primary",
            ),
            InlineKeyboardButton(
                text="✅ Ресурс" if resource else "➕ Ресурс",
                callback_data=f"bestedit:resource:{order.id}",
                style="success" if resource else "primary",
            ),
        ],
    ]
    if order.media:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🗑 Убрать конкретное фото",
                    callback_data=f"bestedit:photos:{order.id}",
                    style="danger",
                )
            ]
        )
    remove_buttons: list[InlineKeyboardButton] = []
    if contact:
        remove_buttons.append(
            InlineKeyboardButton(
                text="🗑 Контакт",
                callback_data=f"bestedit:remove_contact:{order.id}",
                style="danger",
            )
        )
    if resource:
        remove_buttons.append(
            InlineKeyboardButton(
                text="🗑 Ресурс",
                callback_data=f"bestedit:remove_resource:{order.id}",
                style="danger",
            )
        )
    if remove_buttons:
        rows.append(remove_buttons)
    rows.append(
        [
            InlineKeyboardButton(
                text="✅ Готово",
                callback_data=f"bestedit:done:{order.id}",
                style="success",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def get_owned_best(user_id: int, order_id: int) -> AdOrder | None:
    async with SessionFactory() as session:
        return await session.scalar(
            select(AdOrder).where(
                AdOrder.id == order_id,
                AdOrder.user_id == user_id,
                AdOrder.tariff_code == TariffCode.BEST.value,
            )
        )


async def edit_card(bot: Bot, order: AdOrder, text: str, markup: InlineKeyboardMarkup) -> None:
    async with SessionFactory() as session:
        card = await session.scalar(
            select(OrderCard).where(
                OrderCard.order_id == order.id,
                OrderCard.kind == BUYER_CARD,
                OrderCard.chat_id == order.user_id,
            )
        )
    if not card:
        async with SessionFactory() as session:
            stored = await session.get(AdOrder, order.id)
            if stored:
                await update_buyer_card(session, bot, stored)
        return
    try:
        await bot.edit_message_text(
            chat_id=card.chat_id,
            message_id=card.message_id,
            text=text,
            reply_markup=markup,
        )
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            try:
                await bot.edit_message_caption(
                    chat_id=card.chat_id,
                    message_id=card.message_id,
                    caption=text,
                    reply_markup=markup,
                )
            except Exception:
                pass


async def show_menu(bot: Bot, order: AdOrder, saved: str | None = None) -> None:
    await edit_card(bot, order, edit_menu_text(order, saved), edit_menu_keyboard(order))


async def after_change(session, bot: Bot, order: AdOrder, saved: str) -> None:
    if order.status == OrderStatus.ACTIVE.value:
        await replace_best_publication(session, bot, order)
    else:
        await session.commit()
        if order.status == OrderStatus.MODERATION.value:
            staff_chat_id = await current_staff_chat_id()
            await send_ad_content_resilient(bot, staff_chat_id, order)
            await update_staff_card(
                session,
                bot,
                order,
                f"<b>🟡 Заявка №{order.id} обновлена покупателем</b>\n"
                "Проверяйте последний отправленный вариант поста.",
            )
    await show_menu(bot, order, saved)


@router.callback_query(F.data.startswith("bestedit:menu:"))
async def open_best_editor(callback: CallbackQuery, state: FSMContext) -> None:
    order_id = int(callback.data.rsplit(":", 1)[1])
    order = await get_owned_best(callback.from_user.id, order_id)
    if not order or order.status in TERMINAL:
        await callback.answer("Этот Best уже нельзя редактировать.", show_alert=True)
        return
    await state.clear()
    await state.update_data(best_edit_order_id=order.id)
    await show_menu(callback.bot, order)
    await callback.answer("Редактор открыт")


@router.callback_query(F.data.startswith("bestedit:done:"))
async def close_best_editor(callback: CallbackQuery, state: FSMContext) -> None:
    order_id = int(callback.data.rsplit(":", 1)[1])
    await state.clear()
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
        if order and order.user_id == callback.from_user.id:
            await update_buyer_card(session, callback.bot, order)
    await callback.answer("Изменения сохранены")


async def set_wait_state(
    callback: CallbackQuery,
    state: FSMContext,
    order_id: int,
    target_state,
    title: str,
    hint: str,
) -> None:
    order = await get_owned_best(callback.from_user.id, order_id)
    if not order or order.status in TERMINAL:
        await callback.answer("Редактирование недоступно.", show_alert=True)
        return
    await state.set_state(target_state)
    await state.update_data(best_edit_order_id=order_id)
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"bestedit:menu:{order_id}")]
        ]
    )
    await edit_card(callback.bot, order, f"<b>{title}</b>\n\n{hint}", markup)
    await callback.answer()


@router.callback_query(F.data.startswith("bestedit:text:"))
async def edit_text_start(callback: CallbackQuery, state: FSMContext) -> None:
    await set_wait_state(
        callback,
        state,
        int(callback.data.rsplit(":", 1)[1]),
        BestEditFlow.waiting_text,
        "📝 Новый текст",
        "Отправьте только новый текст поста. Жирное, подчёркивание и ссылки сохранятся.",
    )


@router.callback_query(F.data.startswith("bestedit:add_photo:"))
async def add_photo_start(callback: CallbackQuery, state: FSMContext) -> None:
    await set_wait_state(
        callback,
        state,
        int(callback.data.rsplit(":", 1)[1]),
        BestEditFlow.waiting_photo,
        "🖼 Добавление фотографии",
        "Отправьте одну фотографию. Её можно будет удалить отдельно в этом же редакторе.",
    )


@router.callback_query(F.data.startswith("bestedit:contact:"))
async def contact_start(callback: CallbackQuery, state: FSMContext) -> None:
    await set_wait_state(
        callback,
        state,
        int(callback.data.rsplit(":", 1)[1]),
        BestEditFlow.waiting_contact,
        "👤 Контакт",
        "Отправьте @username или ссылку на контакт. Существующая кнопка будет заменена.",
    )


@router.callback_query(F.data.startswith("bestedit:resource:"))
async def resource_start(callback: CallbackQuery, state: FSMContext) -> None:
    await set_wait_state(
        callback,
        state,
        int(callback.data.rsplit(":", 1)[1]),
        BestEditFlow.waiting_resource,
        "🔗 Ресурс",
        "Отправьте ссылку на канал, бота, барахолку или сайт. Существующая кнопка будет заменена.",
    )


async def delete_input(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass


@router.message(BestEditFlow.waiting_text, F.text)
async def save_text(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    order_id = int(data["best_edit_order_id"])
    formatted = message.html_text or escape(message.text)
    async with SessionFactory() as session:
        order = await session.scalar(
            select(AdOrder).where(
                AdOrder.id == order_id,
                AdOrder.user_id == message.from_user.id,
            )
        )
        if not order:
            return
        order.content_text = formatted
        await after_change(session, bot, order, "Текст обновлён")
    await state.clear()
    await delete_input(message)


@router.message(BestEditFlow.waiting_photo, F.photo)
async def save_photo(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    order_id = int(data["best_edit_order_id"])
    async with SessionFactory() as session:
        order = await session.scalar(
            select(AdOrder).where(
                AdOrder.id == order_id,
                AdOrder.user_id == message.from_user.id,
            )
        )
        if not order:
            return
        media = list(order.media or [])
        if len(media) >= 8:
            await show_menu(bot, order, "Уже добавлено 8 фотографий")
        else:
            media.append({"type": "photo", "file_id": message.photo[-1].file_id})
            order.media = media
            await after_change(session, bot, order, "Фотография добавлена")
    await state.clear()
    await delete_input(message)


async def save_button_input(message: Message, state: FSMContext, bot: Bot, kind: str) -> None:
    data = await state.get_data()
    order_id = int(data["best_edit_order_id"])
    url = normalize_url(message.text, allow_username=kind == "contact")
    if not url:
        order = await get_owned_best(message.from_user.id, order_id)
        if order:
            await show_menu(bot, order, "Ссылка не распознана")
        await state.clear()
        await delete_input(message)
        return
    async with SessionFactory() as session:
        order = await session.scalar(
            select(AdOrder).where(
                AdOrder.id == order_id,
                AdOrder.user_id == message.from_user.id,
            )
        )
        if not order:
            return
        buttons = [button for button in list(order.buttons or []) if button.get("kind") != kind]
        buttons.append(
            {
                "text": "👤 Связаться" if kind == "contact" else "🔗 Перейти",
                "url": url,
                "kind": kind,
            }
        )
        order.buttons = buttons
        await after_change(
            session,
            bot,
            order,
            "Контакт обновлён" if kind == "contact" else "Ссылка на ресурс обновлена",
        )
    await state.clear()
    await delete_input(message)


@router.message(BestEditFlow.waiting_contact, F.text)
async def save_contact(message: Message, state: FSMContext, bot: Bot) -> None:
    await save_button_input(message, state, bot, "contact")


@router.message(BestEditFlow.waiting_resource, F.text)
async def save_resource(message: Message, state: FSMContext, bot: Bot) -> None:
    await save_button_input(message, state, bot, "resource")


@router.callback_query(F.data.startswith("bestedit:photos:"))
async def photos_to_remove(callback: CallbackQuery) -> None:
    order_id = int(callback.data.rsplit(":", 1)[1])
    order = await get_owned_best(callback.from_user.id, order_id)
    if not order:
        await callback.answer("Заказ не найден.", show_alert=True)
        return
    rows = [
        [
            InlineKeyboardButton(
                text=f"🗑 Фото {index + 1}",
                callback_data=f"bestedit:remove_photo:{order.id}:{index}",
                style="danger",
            )
        ]
        for index, _ in enumerate(order.media or [])
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"bestedit:menu:{order.id}")])
    await edit_card(
        callback.bot,
        order,
        f"<b>Удаление фотографии · Best №{order.id}</b>\n\nВыберите конкретное фото по его порядковому номеру.",
        InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("bestedit:remove_photo:"))
async def remove_photo(callback: CallbackQuery) -> None:
    _, _, raw_id, raw_index = callback.data.split(":", 3)
    order_id, index = int(raw_id), int(raw_index)
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
        media = list(order.media or [])
        if not 0 <= index < len(media):
            await callback.answer("Фото уже удалено.", show_alert=True)
            return
        if len(media) == 1 and not order.content_text:
            await callback.answer("Нельзя оставить пост без текста и фотографий.", show_alert=True)
            return
        media.pop(index)
        order.media = media
        await after_change(session, callback.bot, order, "Фотография удалена")
    await callback.answer("Удалено")


async def remove_button(callback: CallbackQuery, kind: str) -> None:
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
        order.buttons = [button for button in list(order.buttons or []) if button.get("kind") != kind]
        await after_change(
            session,
            callback.bot,
            order,
            "Контакт удалён" if kind == "contact" else "Ссылка на ресурс удалена",
        )
    await callback.answer("Удалено")


@router.callback_query(F.data.startswith("bestedit:remove_contact:"))
async def remove_contact(callback: CallbackQuery) -> None:
    await remove_button(callback, "contact")


@router.callback_query(F.data.startswith("bestedit:remove_resource:"))
async def remove_resource(callback: CallbackQuery) -> None:
    await remove_button(callback, "resource")
