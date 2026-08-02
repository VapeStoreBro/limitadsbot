from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.config import get_settings
from app.db.session import SessionFactory
from app.enums import TariffCode
from app.keyboards import (
    DURATION_NAMES,
    TARIFF_NAMES,
    booking_offer_keyboard,
    order_confirmation_keyboard,
    tariff_selection_keyboard,
)
from app.services.access import ensure_buyer_access
from app.services.orders import (
    find_next_available_slot,
    get_price,
    prices_for_user,
    slot_available,
)
from app.services.price_card import ensure_price_card
from app.services.ui_screen import render_user_screen
from app.states import OrderFlow

router = Router(name="order_selection_v2")
settings = get_settings()

TARIFF_DESCRIPTIONS = {
    TariffCode.STANDARD.value: (
        "⭐ <b>Standard</b>\n"
        "Самостоятельное размещение рекламы. Покупатель сам публикует посты в барахолке. "
        "Активные ссылки и телефоны запрещены."
    ),
    TariffCode.MIDDLE.value: (
        "📌 <b>Middle</b>\n"
        "Самостоятельное размещение со ссылками, один закреп и до двух замен. "
        "Одновременно доступно три места."
    ),
    TariffCode.BEST.value: (
        "👑 <b>Best</b>\n"
        "Основной закреп, до двух кнопок и автоматическая публикация каждые три часа. "
        "Одновременно доступно одно место."
    ),
}


def selection_caption(selected: str | None = None) -> str:
    text = (
        "💎 <b><u>ШАГ 1 ИЗ 4 · ВЫБОР ТАРИФА</u></b>\n\n"
        "Выберите тариф. После выбора появятся сроки и ваша цена."
    )
    if selected:
        text += f"\n\n<b>Вы выбрали:</b> {TARIFF_NAMES[selected]}\n{TARIFF_DESCRIPTIONS[selected]}"
    return text


async def render_price(
    callback: CallbackQuery,
    text: str,
    markup,
) -> None:
    await render_user_screen(
        callback.bot,
        callback.from_user.id,
        text,
        markup,
        source_message=callback.message,
        media_key="price",
        image_path=ensure_price_card(),
    )


@router.callback_query(F.data == "profile:buy")
async def open_price_v2(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    allowed, _ = await ensure_buyer_access(callback.from_user.id, bot)
    if not allowed:
        await callback.answer()
        return
    await state.clear()
    await state.set_state(OrderFlow.choosing_tariff)
    await render_price(callback, selection_caption(), tariff_selection_keyboard())
    await callback.answer("Открываю прайс")


@router.callback_query(OrderFlow.choosing_tariff, F.data.startswith("tariff:"))
async def select_tariff_v2(callback: CallbackQuery, state: FSMContext) -> None:
    tariff = callback.data.split(":", 1)[1]
    async with SessionFactory() as session:
        prices = await prices_for_user(session, callback.from_user.id, tariff)
    await state.update_data(tariff_code=tariff)
    await render_price(
        callback,
        selection_caption(tariff),
        tariff_selection_keyboard(tariff, prices),
    )
    await callback.answer(f"✅ {TARIFF_NAMES[tariff]}")


@router.callback_query(OrderFlow.choosing_tariff, F.data.startswith("duration:"))
async def select_duration_v2(callback: CallbackQuery, state: FSMContext) -> None:
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
        submitting=False,
    )
    await state.set_state(OrderFlow.confirming_selection)

    discount_line = f"\n🎁 Персональная скидка: <b>{discount}%</b>" if discount else ""
    summary = (
        "🧾 <b><u>ШАГ 2 ИЗ 4 · ПРОВЕРКА УСЛОВИЙ</u></b>\n\n"
        f"├ Тариф: <b>{TARIFF_NAMES[tariff]}</b>\n"
        f"├ Срок: <b>{DURATION_NAMES[duration]}</b>\n"
        f"└ Стоимость: <b>{price} ₽</b>{discount_line}\n\n"
        f"{TARIFF_DESCRIPTIONS[tariff]}"
    )

    if next_slot:
        local = next_slot.astimezone(ZoneInfo(settings.timezone))
        await state.update_data(requested_start_at=next_slot.isoformat())
        text = (
            summary
            + "\n\n⚠️ <b>Все места сейчас заняты.</b>\n"
            f"Ближайший запуск: <b>{local:%d.%m.%Y в %H:%M}</b>.\n"
            "Если место освободится раньше, бот предложит его первому в очереди."
        )
        markup = booking_offer_keyboard(int(next_slot.timestamp()))
    else:
        text = summary + "\n\n✅ <b>Место доступно.</b> Продолжайте оформление."
        markup = order_confirmation_keyboard()
    await render_price(callback, text, markup)
    await callback.answer("Срок выбран")


@router.callback_query(F.data == "order:back_tariffs")
async def back_to_tariffs_v2(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(OrderFlow.choosing_tariff)
    await render_price(callback, selection_caption(), tariff_selection_keyboard())
    await callback.answer("Выберите тариф")
