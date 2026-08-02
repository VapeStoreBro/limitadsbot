from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile

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
from app.services.orders import (
    find_next_available_slot,
    get_price,
    prices_for_user,
    slot_available,
)
from app.services.price_card import ensure_price_card
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
        "Самостоятельное размещение со ссылками, один закреп и до двух замен закреплённого поста. "
        "Одновременно доступно три места."
    ),
    TariffCode.BEST.value: (
        "👑 <b>Best</b>\n"
        "Основной закреп, до двух кнопок и автоматическая публикация копии каждые три часа. "
        "Одновременно доступно одно место."
    ),
}


def selection_caption(selected: str | None = None) -> str:
    text = (
        "💎 <b><u>ШАГ 1 ИЗ 4 · ВЫБОР ТАРИФА</u></b>\n\n"
        "Выберите подходящий вариант кнопкой под прайсом. После выбора появятся доступные сроки и ваша персональная цена."
    )
    if selected:
        text += f"\n\n<b>Вы выбрали:</b> {TARIFF_NAMES[selected]}\n{TARIFF_DESCRIPTIONS[selected]}"
    return text


@router.callback_query(F.data == "profile:buy")
async def open_price_v2(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    from app.handlers.customer import access_allowed

    allowed, _ = await access_allowed(callback.from_user.id, bot)
    if not allowed:
        await callback.answer()
        return
    await state.clear()
    await state.set_state(OrderFlow.choosing_tariff)
    await callback.answer("Открываю прайс")
    await bot.send_photo(
        callback.from_user.id,
        FSInputFile(ensure_price_card()),
        caption=selection_caption(),
        reply_markup=tariff_selection_keyboard(),
    )


@router.callback_query(OrderFlow.choosing_tariff, F.data.startswith("tariff:"))
async def select_tariff_v2(callback: CallbackQuery, state: FSMContext) -> None:
    tariff = callback.data.split(":", 1)[1]
    async with SessionFactory() as session:
        prices = await prices_for_user(session, callback.from_user.id, tariff)
    await state.update_data(tariff_code=tariff)
    await callback.message.edit_caption(
        caption=selection_caption(tariff),
        reply_markup=tariff_selection_keyboard(tariff, prices),
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
        await callback.message.edit_caption(
            caption=(
                summary
                + "\n\n⚠️ <b>Все места сейчас заняты.</b>\n"
                f"Ближайший свободный запуск: <b>{local:%d.%m.%Y в %H:%M}</b>.\n"
                "Для бронирования потребуется тестовая предоплата 50%."
            ),
            reply_markup=booking_offer_keyboard(int(next_slot.timestamp())),
        )
    else:
        await callback.message.edit_caption(
            caption=(
                summary
                + "\n\n✅ <b>Место доступно.</b> Нажмите «Продолжить», чтобы загрузить рекламный пост."
            ),
            reply_markup=order_confirmation_keyboard(),
        )
    await callback.answer("Срок выбран")


@router.callback_query(F.data == "order:back_tariffs")
async def back_to_tariffs_v2(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(OrderFlow.choosing_tariff)
    await callback.message.edit_caption(
        caption=selection_caption(),
        reply_markup=tariff_selection_keyboard(),
    )
    await callback.answer("Выберите тариф")
