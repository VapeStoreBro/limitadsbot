from datetime import datetime, timedelta, timezone
from html import escape
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select

from app.config import get_settings
from app.db.session import SessionFactory
from app.enums import OrderStatus, TariffCode
from app.keyboards import (
    DURATION_NAMES,
    TARIFF_NAMES,
    activation_keyboard,
    best_setup_keyboard,
    booking_offer_keyboard,
    moderation_keyboard,
    order_confirmation_keyboard,
    phone_keyboard,
    preview_keyboard,
    tariff_selection_keyboard,
)
from app.models import AdOrder, Payment, User
from app.rules import validate_post
from app.services.media_groups import MediaGroupCollector
from app.services.orders import (
    create_order,
    deposit_amount,
    find_next_available_slot,
    get_price,
    prices_for_user,
    slot_available,
)
from app.services.price_card import ensure_price_card
from app.services.telegram_ads import send_ad_content
from app.services.users import inspect_membership, is_admin, upsert_user
from app.states import OrderFlow

router = Router(name="customer")
collector = MediaGroupCollector()
settings = get_settings()

TARIFF_DETAILS = {
    TariffCode.STANDARD.value: (
        "Самостоятельные публикации. Без активных ссылок, телефонов и кнопок."
    ),
    TariffCode.MIDDLE.value: (
        "Самостоятельные публикации, ссылки разрешены, один закреп и две замены."
    ),
    TariffCode.BEST.value: (
        "Основной закреп, автопубликации каждые 3 часа и до двух кнопок."
    ),
}


def price_caption(selected_tariff: str | None = None) -> str:
    text = (
        "<b>Выберите тариф</b>\n\n"
        "Standard — самостоятельное размещение\n"
        "Middle — размещение + закреп\n"
        "Best — закреп + автопубликации каждые 3 часа"
    )
    if selected_tariff:
        text += f"\n\nВыбрано: <b>✅ {TARIFF_NAMES[selected_tariff]}</b>\n{TARIFF_DETAILS[selected_tariff]}"
    return text


async def access_allowed(user_id: int, bot: Bot) -> tuple[bool, bool]:
    """Return (allowed, admin)."""
    async with SessionFactory() as session:
        user = await session.get(User, user_id)
        admin = await is_admin(session, user_id)
    if admin:
        return True, True
    if not user or not user.phone:
        await bot.send_message(
            user_id,
            "📱 Отправьте номер телефона для продолжения.",
            reply_markup=phone_keyboard(),
        )
        return False, False
    result, _, _ = await inspect_membership(bot, user_id)
    if result != "member":
        await bot.send_message(user_id, "Сначала подтвердите участие через /start.")
        return False, False
    return True, False


async def open_price(message: Message, state: FSMContext, bot: Bot) -> None:
    if not message.from_user:
        return
    allowed, _ = await access_allowed(message.from_user.id, bot)
    if not allowed:
        return
    await state.clear()
    await state.set_state(OrderFlow.choosing_tariff)
    image = ensure_price_card()
    await message.answer_photo(
        FSInputFile(image),
        caption=price_caption(),
        reply_markup=tariff_selection_keyboard(),
    )


@router.callback_query(F.data == "profile:buy")
async def buy_ad(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    allowed, _ = await access_allowed(callback.from_user.id, bot)
    if not allowed:
        await callback.answer()
        return
    await callback.answer()
    await state.clear()
    await state.set_state(OrderFlow.choosing_tariff)
    image = ensure_price_card()
    await bot.send_photo(
        callback.from_user.id,
        FSInputFile(image),
        caption=price_caption(),
        reply_markup=tariff_selection_keyboard(),
    )


@router.callback_query(OrderFlow.choosing_tariff, F.data.startswith("tariff:"))
async def choose_tariff(callback: CallbackQuery, state: FSMContext) -> None:
    tariff = callback.data.split(":", 1)[1]
    async with SessionFactory() as session:
        prices = await prices_for_user(session, callback.from_user.id, tariff)
    await state.update_data(tariff_code=tariff)
    await callback.message.edit_caption(
        caption=price_caption(tariff),
        reply_markup=tariff_selection_keyboard(tariff, prices),
    )
    await callback.answer(f"Выбран {TARIFF_NAMES[tariff]}")


@router.callback_query(OrderFlow.choosing_tariff, F.data.startswith("duration:"))
async def choose_duration(callback: CallbackQuery, state: FSMContext) -> None:
    _, tariff, duration = callback.data.split(":", 2)
    now = datetime.now(timezone.utc)
    async with SessionFactory() as session:
        price, hours, discount = await get_price(
            session,
            callback.from_user.id,
            tariff,
            duration,
        )
        available = await slot_available(
            session,
            tariff,
            now,
            now + timedelta(hours=hours),
        )
        next_slot = None
        if tariff != TariffCode.STANDARD.value and not available:
            next_slot = await find_next_available_slot(session, tariff, hours, now)

    await state.update_data(
        tariff_code=tariff,
        duration_code=duration,
        price_rub=price,
        duration_hours=hours,
        requested_start_at=None,
        content_text=None,
        validation_text=None,
        media=[],
        buttons=[],
    )
    await state.set_state(OrderFlow.confirming_selection)

    discount_line = f"\nВаша скидка: <b>{discount}%</b>" if discount else ""
    if next_slot:
        local = next_slot.astimezone(ZoneInfo(settings.timezone))
        await state.update_data(requested_start_at=next_slot.isoformat())
        await callback.message.edit_caption(
            caption=(
                f"<b>{TARIFF_NAMES[tariff]}</b>\n"
                f"Срок: <b>{DURATION_NAMES[duration]}</b>\n"
                f"Стоимость: <b>{price} ₽</b>{discount_line}\n\n"
                "Сейчас все места заняты.\n"
                f"Ближайший свободный запуск: <b>{local:%d.%m.%Y в %H:%M}</b>\n"
                "Бронирование — предоплата 50%."
            ),
            reply_markup=booking_offer_keyboard(int(next_slot.timestamp())),
        )
    else:
        await callback.message.edit_caption(
            caption=(
                f"<b>{TARIFF_NAMES[tariff]}</b>\n"
                f"Срок: <b>{DURATION_NAMES[duration]}</b>\n"
                f"Стоимость: <b>{price} ₽</b>{discount_line}\n\n"
                f"{TARIFF_DETAILS[tariff]}"
            ),
            reply_markup=order_confirmation_keyboard(),
        )
    await callback.answer()


@router.callback_query(OrderFlow.confirming_selection, F.data == "order:continue")
async def continue_order(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(requested_start_at=None)
    await state.set_state(OrderFlow.waiting_post)
    await callback.message.edit_caption(
        caption=(
            "<b>Пришлите готовый рекламный пост</b>\n\n"
            "Можно отправить текст или альбом до 8 фотографий.\n"
            "Видео и GIF не принимаются."
        ),
        reply_markup=None,
    )
    await callback.answer()


@router.callback_query(OrderFlow.confirming_selection, F.data.startswith("order:book:"))
async def confirm_booking(callback: CallbackQuery, state: FSMContext) -> None:
    timestamp = int(callback.data.rsplit(":", 1)[1])
    requested = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    await state.update_data(requested_start_at=requested.isoformat())
    await state.set_state(OrderFlow.waiting_post)
    await callback.message.edit_caption(
        caption=(
            "<b>Место выбрано для бронирования</b>\n\n"
            "Пришлите готовый рекламный пост или альбом до 8 фотографий."
        ),
        reply_markup=None,
    )
    await callback.answer()


@router.callback_query(F.data == "order:back_tariffs")
async def back_to_tariffs(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(OrderFlow.choosing_tariff)
    await callback.message.edit_caption(
        caption=price_caption(),
        reply_markup=tariff_selection_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "order:cancel")
async def cancel_order(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        if callback.message.photo:
            await callback.message.edit_caption(caption="Оформление отменено.", reply_markup=None)
        else:
            await callback.message.edit_text("Оформление отменено.", reply_markup=None)
    except Exception:
        await callback.message.answer("Оформление отменено.")
    await callback.answer()


async def process_post(messages: list[Message], state: FSMContext, bot: Bot) -> None:
    first = messages[0]
    data = await state.get_data()
    tariff = data.get("tariff_code")
    if not tariff:
        await first.answer("Начните оформление заново через профиль.")
        return

    source = next((item for item in messages if item.caption), first)
    plain_text = source.caption or source.text or ""
    formatted_text = source.html_caption if source.caption else source.html_text or ""
    entities = list(source.caption_entities or source.entities or [])
    media: list[dict[str, str]] = []

    for item in messages:
        if item.photo:
            media.append({"type": "photo", "file_id": item.photo[-1].file_id})
        elif item.video or item.animation or item.document:
            await first.answer("❌ Разрешены только текст и фотографии.")
            return

    if tariff == TariffCode.STANDARD.value and any(
        str(entity.type) in {"url", "text_link", "phone_number"} for entity in entities
    ):
        await first.answer("❌ В Standard запрещены активные ссылки и телефоны.")
        return

    result = validate_post(tariff, plain_text, media, [])
    if not result.ok:
        await first.answer(f"❌ {result.error}")
        return

    await state.update_data(
        content_text=formatted_text,
        validation_text=plain_text,
        media=media,
        buttons=[],
        waiting_button=False,
    )
    if tariff == TariffCode.BEST.value:
        await state.set_state(OrderFlow.adding_buttons)
        await first.answer(
            "Можно добавить до двух кнопок.",
            reply_markup=best_setup_keyboard(0),
        )
        return
    await show_preview(first, state, bot)


@router.message(OrderFlow.waiting_post)
async def receive_post(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.media_group_id:
        collector.add(message, lambda items: process_post(items, state, bot))
    else:
        await process_post([message], state, bot)


@router.callback_query(OrderFlow.adding_buttons, F.data == "best:add_button")
async def ask_best_button(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    buttons = list(data.get("buttons", []))
    if len(buttons) >= 2:
        await callback.answer("Уже добавлены две кнопки.", show_alert=True)
        return
    await state.update_data(waiting_button=True)
    await callback.message.answer(
        "Отправьте кнопку в формате:\n<code>Название | https://ссылка</code>"
    )
    await callback.answer()


@router.message(OrderFlow.adding_buttons, F.text)
async def receive_best_button(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("waiting_button"):
        await message.answer("Используйте кнопки под сообщением.")
        return
    buttons = list(data.get("buttons", []))
    if "|" not in message.text or len(buttons) >= 2:
        await message.answer("Формат: <code>Название | https://ссылка</code>")
        return
    title, url = (part.strip() for part in message.text.split("|", 1))
    candidate = buttons + [{"text": title[:64], "url": url}]
    result = validate_post(
        data["tariff_code"],
        data.get("validation_text", ""),
        data.get("media", []),
        candidate,
    )
    if not result.ok:
        await message.answer(f"❌ {result.error}")
        return
    await state.update_data(buttons=candidate, waiting_button=False)
    await message.answer(
        f"✅ Кнопка добавлена ({len(candidate)}/2).",
        reply_markup=best_setup_keyboard(len(candidate)),
    )


@router.callback_query(OrderFlow.adding_buttons, F.data == "best:preview")
async def best_to_preview(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await callback.answer()
    await show_preview(callback.message, state, bot)


async def show_preview(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    preview = SimpleNamespace(
        content_text=data.get("content_text", ""),
        media=data.get("media", []),
        buttons=data.get("buttons", []),
    )
    await message.answer("<b>Предпросмотр рекламы:</b>")
    await send_ad_content(bot, message.chat.id, preview)
    requested = data.get("requested_start_at")
    booking_line = ""
    if requested:
        local = datetime.fromisoformat(requested).astimezone(ZoneInfo(settings.timezone))
        booking_line = f"\nБронь: <b>{local:%d.%m.%Y %H:%M}</b>"
    await state.set_state(OrderFlow.previewing)
    await message.answer(
        f"Тариф: <b>{TARIFF_NAMES[data['tariff_code']]}</b>\n"
        f"Срок: <b>{DURATION_NAMES[data['duration_code']]}</b>\n"
        f"Стоимость: <b>{data['price_rub']} ₽</b>{booking_line}",
        reply_markup=preview_keyboard(),
    )


@router.callback_query(OrderFlow.previewing, F.data == "preview:redo")
async def redo_post(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(
        content_text=None,
        validation_text=None,
        media=[],
        buttons=[],
        waiting_button=False,
    )
    await state.set_state(OrderFlow.waiting_post)
    await callback.message.edit_text(
        "Пришлите новый рекламный пост или альбом до 8 фотографий.",
        reply_markup=None,
    )
    await callback.answer()


@router.callback_query(OrderFlow.previewing, F.data == "preview:submit")
async def submit_preview(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    requested = (
        datetime.fromisoformat(data["requested_start_at"])
        if data.get("requested_start_at")
        else None
    )
    async with SessionFactory() as session:
        if requested and not await slot_available(
            session,
            data["tariff_code"],
            requested,
            requested + timedelta(hours=data["duration_hours"]),
        ):
            await callback.answer("Это место уже заняли. Начните оформление заново.", show_alert=True)
            await state.clear()
            return

        order = await create_order(
            session,
            user_id=callback.from_user.id,
            tariff_code=data["tariff_code"],
            duration_code=data["duration_code"],
            content_text=data.get("content_text", ""),
            media=data.get("media", []),
            buttons=data.get("buttons", []),
            requested_start_at=requested,
        )
        user = await session.get(User, callback.from_user.id)

        full_name = " ".join(
            part for part in [user.first_name, user.last_name] if part
        ).strip()
        await bot.send_message(
            settings.staff_chat_id,
            f"🆕 <b>Заявка №{order.id}</b>\n\n"
            f"Клиент: <a href=\"tg://user?id={user.id}\">{escape(full_name or str(user.id))}</a>\n"
            f"ID: <code>{user.id}</code>\n"
            f"Username: @{escape(user.username) if user.username else 'нет'}\n"
            f"Телефон: <code>{escape(user.phone or 'не указан')}</code>\n"
            f"Тариф: <b>{TARIFF_NAMES[order.tariff_code]}</b>\n"
            f"Срок: <b>{DURATION_NAMES[order.duration_code]}</b>\n"
            f"Цена: <b>{order.price_rub} ₽</b>\n"
            f"Бронь: <b>{order.requested_start_at or 'нет'}</b>",
        )
        await send_ad_content(bot, settings.staff_chat_id, order)
        card = await bot.send_message(
            settings.staff_chat_id,
            f"Решение по заявке №{order.id}:",
            reply_markup=moderation_keyboard(order.id),
        )
        order.moderation_card_message_id = card.message_id
        await session.commit()

    await state.clear()
    await callback.message.edit_text(
        f"✅ Заявка №{order.id} отправлена администрации.",
        reply_markup=None,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("testpay:"))
async def test_payment(callback: CallbackQuery, bot: Bot) -> None:
    _, raw_id, kind = callback.data.split(":", 2)
    order_id = int(raw_id)
    now = datetime.now(timezone.utc)
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
        if not order or order.user_id != callback.from_user.id:
            await callback.answer("Заказ не найден.", show_alert=True)
            return
        if kind == "deposit":
            amount = deposit_amount(order.price_rub)
            if order.paid_rub:
                await callback.answer("Предоплата уже внесена.", show_alert=True)
                return
            order.paid_rub = amount
            order.status = OrderStatus.BOOKED.value
        elif kind == "remainder":
            amount = order.price_rub - order.paid_rub
            order.paid_rub = order.price_rub
            order.status = (
                OrderStatus.READY.value
                if order.requested_start_at and order.requested_start_at <= now
                else OrderStatus.BOOKED.value
            )
        else:
            amount = order.price_rub - order.paid_rub
            order.paid_rub = order.price_rub
            order.status = OrderStatus.READY.value
        session.add(
            Payment(
                order_id=order.id,
                provider="test",
                amount_rub=amount,
                status="paid",
                external_id=f"test-{order.id}-{int(now.timestamp())}",
                created_at=now,
                paid_at=now,
            )
        )
        order.updated_at = now
        await session.commit()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Тестовая оплата прошла!", show_alert=True)
    if order.status == OrderStatus.READY.value:
        await bot.send_message(
            settings.staff_chat_id,
            f"💳 Заказ №{order.id} полностью оплачен. Таймер ещё не запущен.",
            reply_markup=activation_keyboard(order.id),
        )
    else:
        await bot.send_message(
            settings.staff_chat_id,
            f"📅 По брони №{order.id} внесено {order.paid_rub}/{order.price_rub} ₽.",
        )


async def send_my_orders(user_id: int, bot: Bot) -> None:
    async with SessionFactory() as session:
        orders = (
            await session.scalars(
                select(AdOrder)
                .where(AdOrder.user_id == user_id)
                .order_by(AdOrder.id.desc())
                .limit(10)
            )
        ).all()
    if not orders:
        await bot.send_message(user_id, "У вас пока нет рекламных заявок.")
        return

    lines = ["<b>Мои рекламы</b>"]
    buttons: list[list[InlineKeyboardButton]] = []
    for order in orders:
        lines.append(
            f"\n№{order.id} · {TARIFF_NAMES.get(order.tariff_code, order.tariff_code)}"
            f"\n{DURATION_NAMES.get(order.duration_code, order.duration_code)} · {order.price_rub} ₽"
            f"\nСтатус: <b>{order.status}</b>"
        )
        if order.status == OrderStatus.BOOKED.value and order.paid_rub < order.price_rub:
            remaining = order.price_rub - order.paid_rub
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"💳 Доплатить {remaining} ₽ по №{order.id}",
                        callback_data=f"testpay:{order.id}:remainder",
                        style="success",
                    )
                ]
            )
        if (
            order.status == OrderStatus.ACTIVE.value
            and order.tariff_code == TariffCode.MIDDLE.value
            and order.pin_changes_used < 2
        ):
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"📌 Сменить закреп №{order.id}",
                        callback_data=f"middlepin:{order.id}",
                    )
                ]
            )
    buttons.append([InlineKeyboardButton(text="⬅️ Профиль", callback_data="profile:home")])
    await bot.send_message(
        user_id,
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data == "profile:orders")
async def my_orders_callback(callback: CallbackQuery, bot: Bot) -> None:
    await callback.answer()
    await send_my_orders(callback.from_user.id, bot)


@router.callback_query(F.data.startswith("middlepin:"))
async def request_middle_pin(callback: CallbackQuery) -> None:
    order_id = int(callback.data.split(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
        if (
            not order
            or order.user_id != callback.from_user.id
            or order.status != OrderStatus.ACTIVE.value
        ):
            await callback.answer("Закреп недоступен.", show_alert=True)
            return
        if order.tariff_code != TariffCode.MIDDLE.value or order.pin_changes_used >= 2:
            await callback.answer("Лимит двух замен исчерпан.", show_alert=True)
            return
        order.awaiting_middle_pin = True
        order.updated_at = datetime.now(timezone.utc)
        await session.commit()
    await callback.answer("Следующее сообщение в барахолке станет закрепом.", show_alert=True)
