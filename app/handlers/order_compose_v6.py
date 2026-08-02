from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import get_settings
from app.enums import TariffCode
from app.handlers.order_flow_v2 import formatted_message_text, upload_instructions
from app.keyboards import DURATION_NAMES, TARIFF_NAMES
from app.keyboards_v3 import simplified_best_keyboard, upload_navigation_keyboard
from app.rules import validate_post
from app.services.media_groups import MediaGroupCollector
from app.services.ui_screen import delete_user_input, render_user_screen
from app.states import OrderFlow

router = Router(name="order_compose_v6")
collector = MediaGroupCollector()
settings = get_settings()


async def render(
    callback: CallbackQuery,
    text: str,
    markup: InlineKeyboardMarkup,
    *,
    media_key: str = "main",
    photo_file_id: str | None = None,
) -> None:
    await render_user_screen(
        callback.bot,
        callback.from_user.id,
        text,
        markup,
        source_message=callback.message,
        media_key=media_key,
        photo_file_id=photo_file_id,
    )


@router.callback_query(OrderFlow.confirming_selection, F.data == "order:continue")
async def continue_order(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    tariff = data.get("tariff_code")
    if not tariff:
        await callback.answer("Выберите тариф заново.", show_alert=True)
        return
    await state.update_data(requested_start_at=None)
    await state.set_state(OrderFlow.waiting_post)
    await render(
        callback,
        upload_instructions(tariff),
        upload_navigation_keyboard(),
    )
    await callback.answer("Жду ваш пост")


@router.callback_query(OrderFlow.confirming_selection, F.data.startswith("order:book:"))
async def confirm_booking(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    tariff = data.get("tariff_code")
    if not tariff:
        await callback.answer("Выберите тариф заново.", show_alert=True)
        return
    timestamp = int(callback.data.rsplit(":", 1)[1])
    requested = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    await state.update_data(requested_start_at=requested.isoformat())
    await state.set_state(OrderFlow.waiting_post)
    await render(
        callback,
        upload_instructions(tariff, booked=True),
        upload_navigation_keyboard(),
    )
    await callback.answer("Место забронировано")


def draft_preview_markup(index: int, total: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if total > 1:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⬅️ Фото",
                    callback_data=f"draftpreview:{(index - 1) % total}",
                ),
                InlineKeyboardButton(text=f"{index + 1}/{total}", callback_data="noop"),
                InlineKeyboardButton(
                    text="Фото ➡️",
                    callback_data=f"draftpreview:{(index + 1) % total}",
                ),
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="✅ Отправить на модерацию",
                    callback_data="preview:submit",
                    style="success",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Загрузить пост заново",
                    callback_data="preview:redo",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 Отменить и в меню",
                    callback_data="nav:home",
                    style="danger",
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def preview_text(data: dict, index: int) -> str:
    booking = ""
    if data.get("requested_start_at"):
        local = datetime.fromisoformat(data["requested_start_at"]).astimezone(
            ZoneInfo(settings.timezone)
        )
        booking = f"\n├ Бронь: <b>{local:%d.%m.%Y в %H:%M}</b>"
    media = data.get("media", [])
    buttons = data.get("buttons", [])
    return (
        "<b><u>👁 ПРЕДПРОСМОТР ЗАКАЗА</u></b>\n\n"
        f"├ Тариф: <b>{TARIFF_NAMES[data['tariff_code']]}</b>\n"
        f"├ Срок: <b>{DURATION_NAMES[data['duration_code']]}</b>\n"
        f"├ Стоимость: <b>{data['price_rub']} ₽</b>{booking}\n"
        f"├ Фотографий: <b>{len(media)}</b>"
        + (f" · показано <b>{index + 1}</b>" if media else "")
        + f"\n└ Кнопок: <b>{len(buttons)}</b>\n\n"
        "Проверьте данные и отправьте заявку на модерацию."
    )


async def show_preview_for_user(
    bot: Bot,
    user_id: int,
    state: FSMContext,
    *,
    index: int = 0,
    source_message: Message | None = None,
) -> None:
    data = await state.get_data()
    media = list(data.get("media", []))
    index = max(0, min(index, len(media) - 1)) if media else 0
    await state.set_state(OrderFlow.previewing)
    await render_user_screen(
        bot,
        user_id,
        preview_text(data, index),
        draft_preview_markup(index, len(media)),
        source_message=source_message,
        media_key=f"draft:{index}" if media else "main",
        photo_file_id=media[index]["file_id"] if media else None,
    )


async def process_post(messages: list[Message], state: FSMContext, bot: Bot) -> None:
    first = messages[0]
    user_id = first.from_user.id if first.from_user else 0
    data = await state.get_data()
    tariff = data.get("tariff_code")
    if not tariff or not user_id:
        for item in messages:
            await delete_user_input(item)
        return

    source = next(
        (item for item in messages if item.caption is not None or item.text is not None),
        first,
    )
    plain_text, formatted_text, entities = formatted_message_text(source)
    media: list[dict[str, str]] = []
    invalid_format = False
    for item in messages:
        if item.photo:
            media.append({"type": "photo", "file_id": item.photo[-1].file_id})
        elif item.text is None:
            invalid_format = True

    if len(media) > 8:
        error = "Можно загрузить не больше 8 фотографий."
    elif invalid_format:
        error = "Принимаются только текст, фотография или фотоальбом."
    else:
        entity_types = {
            getattr(entity.type, "value", str(entity.type)) for entity in entities
        }
        if tariff == TariffCode.STANDARD.value and entity_types.intersection(
            {"url", "text_link", "phone_number"}
        ):
            error = "В Standard нельзя использовать активную ссылку или номер телефона."
        else:
            result = validate_post(tariff, plain_text, media, [])
            error = result.error if not result.ok else None

    for item in messages:
        await delete_user_input(item)

    if error:
        await render_user_screen(
            bot,
            user_id,
            "<b>❌ Пост не принят</b>\n\n"
            f"Причина: <b>{escape(error or 'неизвестная ошибка')}</b>\n\n"
            "Исправьте материал и отправьте его ещё раз.",
            upload_navigation_keyboard(),
            media_key="main",
        )
        return

    await state.update_data(
        content_text=formatted_text,
        validation_text=plain_text,
        media=media,
        buttons=[],
        waiting_button=False,
        submitting=False,
    )

    if tariff == TariffCode.BEST.value:
        await state.set_state(OrderFlow.adding_buttons)
        await render_user_screen(
            bot,
            user_id,
            "<b><u>🔗 ШАГ 4 · КНОПКИ BEST</u></b>\n\n"
            "Добавьте контакт и/или ресурс. Готовые части будут отмечены зелёным. Кнопки необязательны.",
            simplified_best_keyboard(0),
            media_key="main",
        )
        return

    await show_preview_for_user(bot, user_id, state)


@router.message(OrderFlow.waiting_post)
async def receive_post(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.media_group_id:
        collector.add(message, lambda items: process_post(items, state, bot))
        return
    await process_post([message], state, bot)


@router.callback_query(OrderFlow.adding_buttons, F.data == "best:preview")
async def best_preview(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await show_preview_for_user(
        bot,
        callback.from_user.id,
        state,
        source_message=callback.message,
    )
    await callback.answer("Предпросмотр готов")


@router.callback_query(OrderFlow.previewing, F.data.startswith("draftpreview:"))
async def draft_preview_page(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    index = int(callback.data.rsplit(":", 1)[1])
    await show_preview_for_user(
        bot,
        callback.from_user.id,
        state,
        index=index,
        source_message=callback.message,
    )
    await callback.answer()


@router.callback_query(OrderFlow.previewing, F.data == "preview:redo")
async def redo_post(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.update_data(
        content_text=None,
        validation_text=None,
        media=[],
        buttons=[],
        waiting_button=False,
        submitting=False,
    )
    await state.set_state(OrderFlow.waiting_post)
    await render(
        callback,
        upload_instructions(data.get("tariff_code", TariffCode.STANDARD.value)),
        upload_navigation_keyboard(),
    )
    await callback.answer("Отправьте новый пост")
