from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from app.config import get_settings
from app.db.session import SessionFactory
from app.enums import OrderStatus, TariffCode
from app.keyboards import (
    DURATION_NAMES,
    TARIFF_NAMES,
    activation_keyboard,
    durations_keyboard,
    moderation_keyboard,
    start_mode_keyboard,
    tariffs_keyboard,
)
from app.models import AdOrder, Payment
from app.rules import validate_post
from app.services.media_groups import MediaGroupCollector
from app.services.orders import create_order, deposit_amount, get_price, prices_for_user, slot_available
from app.services.telegram_ads import send_ad_content
from app.services.users import upsert_user
from app.states import OrderFlow

router = Router(name="customer")
collector = MediaGroupCollector()
settings = get_settings()


async def ensure_member(message: Message, bot: Bot) -> bool:
    if not message.from_user:
        return False
    async with SessionFactory() as session:
        user = await upsert_user(session, bot, message.from_user)
    if not user.is_bazaar_member:
        await message.answer("❌ Купить рекламу могут только участники тестовой барахолки.")
        return False
    return True


@router.message(F.text == "📢 Купить рекламу")
async def buy_ad(message: Message, state: FSMContext, bot: Bot) -> None:
    if not await ensure_member(message, bot):
        return
    await state.clear()
    await state.set_state(OrderFlow.choosing_tariff)
    await message.answer("Выберите тариф:", reply_markup=tariffs_keyboard())


@router.callback_query(OrderFlow.choosing_tariff, F.data.startswith("tariff:"))
async def choose_tariff(callback: CallbackQuery, state: FSMContext) -> None:
    tariff = callback.data.split(":", 1)[1]
    async with SessionFactory() as session:
        prices = await prices_for_user(session, callback.from_user.id, tariff)
    await state.update_data(tariff_code=tariff)
    await state.set_state(OrderFlow.choosing_duration)
    await callback.message.edit_text(
        f"Тариф: <b>{TARIFF_NAMES[tariff]}</b>\nВыберите длительность:",
        reply_markup=durations_keyboard(tariff, prices),
    )
    await callback.answer()


@router.callback_query(OrderFlow.choosing_duration, F.data.startswith("duration:"))
async def choose_duration(callback: CallbackQuery, state: FSMContext) -> None:
    _, tariff, duration = callback.data.split(":", 2)
    await state.update_data(tariff_code=tariff, duration_code=duration)
    if tariff in {TariffCode.MIDDLE.value, TariffCode.BEST.value}:
        await state.set_state(OrderFlow.choosing_start_mode)
        await callback.message.edit_text("Выберите способ запуска:", reply_markup=start_mode_keyboard())
    else:
        await state.update_data(requested_start_at=None)
        await state.set_state(OrderFlow.waiting_post)
        await callback.message.edit_text("Пришлите готовый пост или фотоальбом до восьми фотографий.")
    await callback.answer()


@router.callback_query(OrderFlow.choosing_start_mode, F.data.startswith("startmode:"))
async def choose_start_mode(callback: CallbackQuery, state: FSMContext) -> None:
    mode = callback.data.split(":", 1)[1]
    if mode == "book":
        await state.set_state(OrderFlow.entering_booking_date)
        await callback.message.edit_text(
            "Введите дату и время запуска по Москве: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>.\n"
            "Предоплата — 50%, остаток не позднее чем за 24 часа."
        )
    else:
        await state.update_data(requested_start_at=None)
        await state.set_state(OrderFlow.waiting_post)
        await callback.message.edit_text("Пришлите готовый пост или фотоальбом до восьми фотографий.")
    await callback.answer()


@router.message(OrderFlow.entering_booking_date, F.text)
async def booking_date(message: Message, state: FSMContext) -> None:
    try:
        local = datetime.strptime(message.text.strip(), "%d.%m.%Y %H:%M").replace(
            tzinfo=ZoneInfo(settings.timezone)
        )
        requested = local.astimezone(timezone.utc)
    except ValueError:
        await message.answer("Неверный формат. Пример: <code>15.08.2026 18:30</code>")
        return
    if requested < datetime.now(timezone.utc) + timedelta(hours=1):
        await message.answer("Бронирование должно быть минимум за один час.")
        return
    await state.update_data(requested_start_at=requested.isoformat())
    await state.set_state(OrderFlow.waiting_post)
    await message.answer("Теперь пришлите готовый пост или фотоальбом.")


async def process_post(messages: list[Message], state: FSMContext, bot: Bot) -> None:
    first = messages[0]
    data = await state.get_data()
    tariff = data["tariff_code"]
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
    )
    if tariff == TariffCode.BEST.value:
        await state.set_state(OrderFlow.adding_buttons)
        await first.answer(
            "Best разрешает до двух кнопок. Каждую пришлите так:\n"
            "<code>Название | https://ссылка</code>\n"
            "После добавления напишите <code>готово</code>."
        )
        return
    await finalize_order(first, state, bot)


@router.message(OrderFlow.waiting_post)
async def receive_post(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.media_group_id:
        collector.add(message, lambda items: process_post(items, state, bot))
    else:
        await process_post([message], state, bot)


@router.message(OrderFlow.adding_buttons, F.text)
async def add_buttons(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    buttons = list(data.get("buttons", []))
    if message.text.strip().lower() == "готово":
        result = validate_post(
            data["tariff_code"],
            data.get("validation_text", ""),
            data["media"],
            buttons,
        )
        if not result.ok:
            await message.answer(f"❌ {result.error}")
            return
        await finalize_order(message, state, bot)
        return
    if len(buttons) >= 2 or "|" not in message.text:
        await message.answer("Формат: <code>Название | https://ссылка</code>, максимум две кнопки.")
        return
    title, url = (part.strip() for part in message.text.split("|", 1))
    candidate = buttons + [{"text": title[:64], "url": url}]
    result = validate_post(
        data["tariff_code"],
        data.get("validation_text", ""),
        data["media"],
        candidate,
    )
    if not result.ok:
        await message.answer(f"❌ {result.error}")
        return
    await state.update_data(buttons=candidate)
    await message.answer(f"✅ Кнопка добавлена ({len(candidate)}/2).")


async def finalize_order(message: Message, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    requested = datetime.fromisoformat(data["requested_start_at"]) if data.get("requested_start_at") else None
    async with SessionFactory() as session:
        _, hours, _ = await get_price(
            session,
            message.from_user.id,
            data["tariff_code"],
            data["duration_code"],
        )
        if requested and not await slot_available(
            session,
            data["tariff_code"],
            requested,
            requested + timedelta(hours=hours),
        ):
            await message.answer("❌ На это время нет свободного места.")
            await state.clear()
            return
        order = await create_order(
            session,
            user_id=message.from_user.id,
            tariff_code=data["tariff_code"],
            duration_code=data["duration_code"],
            content_text=data["content_text"],
            media=data["media"],
            buttons=data.get("buttons", []),
            requested_start_at=requested,
        )
        await bot.send_message(
            settings.staff_chat_id,
            f"🆕 <b>Заявка №{order.id}</b>\n"
            f"Клиент: <a href=\"tg://user?id={order.user_id}\">{order.user_id}</a>\n"
            f"Тариф: {TARIFF_NAMES[order.tariff_code]}\n"
            f"Период: {DURATION_NAMES[order.duration_code]}\n"
            f"Цена: {order.price_rub} ₽\n"
            f"Бронь: {order.requested_start_at or 'нет'}",
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
    await message.answer(f"✅ Заявка №{order.id} отправлена администрации. Цена: {order.price_rub} ₽.")


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


@router.message(F.text == "📦 Мои заказы")
async def my_orders(message: Message) -> None:
    async with SessionFactory() as session:
        orders = (
            await session.scalars(
                select(AdOrder)
                .where(AdOrder.user_id == message.from_user.id)
                .order_by(AdOrder.id.desc())
                .limit(10)
            )
        ).all()
    if not orders:
        await message.answer("Заказов пока нет.")
        return
    lines = ["<b>Последние заказы:</b>"]
    buttons: list[list[InlineKeyboardButton]] = []
    for order in orders:
        lines.append(f"№{order.id} · {order.tariff_code} · {order.price_rub} ₽ · {order.status}")
        if order.status == OrderStatus.BOOKED.value and order.paid_rub < order.price_rub:
            remaining = order.price_rub - order.paid_rub
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=f"Доплатить {remaining} ₽ по №{order.id}",
                        callback_data=f"testpay:{order.id}:remainder",
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
    markup = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
    await message.answer("\n".join(lines), reply_markup=markup)


@router.callback_query(F.data.startswith("middlepin:"))
async def request_middle_pin(callback: CallbackQuery) -> None:
    order_id = int(callback.data.split(":", 1)[1])
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
        if not order or order.user_id != callback.from_user.id or order.status != OrderStatus.ACTIVE.value:
            await callback.answer("Закреп недоступен.", show_alert=True)
            return
        if order.tariff_code != TariffCode.MIDDLE.value or order.pin_changes_used >= 2:
            await callback.answer("Лимит двух замен исчерпан.", show_alert=True)
            return
        order.awaiting_middle_pin = True
        order.updated_at = datetime.now(timezone.utc)
        await session.commit()
    await callback.answer("Следующее сообщение в барахолке станет закрепом.", show_alert=True)
