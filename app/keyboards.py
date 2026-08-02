from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.enums import DurationCode, TariffCode

TARIFF_NAMES = {
    TariffCode.STANDARD.value: "Standard 🎪",
    TariffCode.MIDDLE.value: "Middle 🎭",
    TariffCode.BEST.value: "Best 🔥",
}
DURATION_NAMES = {
    DurationCode.DAY.value: "1 день",
    DurationCode.WEEK.value: "7 дней",
    DurationCode.MONTH.value: "30 дней",
}


def phone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="📱 Отправить номер",
                    request_contact=True,
                    style="success",
                )
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="Нажмите кнопку ниже",
    )


def profile_keyboard(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="📢 Разместить рекламу",
                callback_data="profile:buy",
                style="success",
            )
        ],
        [InlineKeyboardButton(text="📂 Мои рекламы", callback_data="profile:orders")],
    ]
    if is_admin:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔐 Админ-панель",
                    callback_data="profile:admin",
                    style="primary",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def membership_keyboard(group_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Вступить в группу", url=group_url, style="primary")],
            [
                InlineKeyboardButton(
                    text="🔄 Проверить снова",
                    callback_data="profile:recheck",
                    style="success",
                )
            ],
        ]
    )


def admin_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📥 Заявки"), KeyboardButton(text="🚀 Активная реклама")],
            [KeyboardButton(text="📅 Бронирования"), KeyboardButton(text="👥 Клиенты")],
            [KeyboardButton(text="💳 Платежи"), KeyboardButton(text="💰 Персональные цены")],
            [KeyboardButton(text="🏷 Тарифы"), KeyboardButton(text="👮 Администраторы")],
            [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="⬅️ Профиль")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Админ-панель",
    )


def tariff_selection_keyboard(
    selected_tariff: str | None = None,
    prices: dict[str, int] | None = None,
) -> InlineKeyboardMarkup:
    tariff_row: list[InlineKeyboardButton] = []
    for code in (TariffCode.STANDARD.value, TariffCode.MIDDLE.value, TariffCode.BEST.value):
        selected = code == selected_tariff
        tariff_row.append(
            InlineKeyboardButton(
                text=("✅ " if selected else "") + TARIFF_NAMES[code],
                callback_data=f"tariff:{code}",
                style="success" if selected else None,
            )
        )
    rows: list[list[InlineKeyboardButton]] = [tariff_row]
    if selected_tariff and prices:
        for duration in (
            DurationCode.DAY.value,
            DurationCode.WEEK.value,
            DurationCode.MONTH.value,
        ):
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{DURATION_NAMES[duration]} — {prices[duration]} ₽",
                        callback_data=f"duration:{selected_tariff}:{duration}",
                        style="primary",
                    )
                ]
            )
    rows.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="order:cancel", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def order_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➡️ Продолжить",
                    callback_data="order:continue",
                    style="success",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="order:back_tariffs")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="order:cancel", style="danger")],
        ]
    )


def booking_offer_keyboard(start_timestamp: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📅 Забронировать место — 50%",
                    callback_data=f"order:book:{start_timestamp}",
                    style="success",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Другой тариф", callback_data="order:back_tariffs")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="order:cancel", style="danger")],
        ]
    )


def best_setup_keyboard(button_count: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if button_count < 2:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"➕ Добавить кнопку ({button_count}/2)",
                    callback_data="best:add_button",
                    style="primary",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="✅ Перейти к предпросмотру",
                callback_data="best:preview",
                style="success",
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="order:cancel", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def preview_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Отправить на модерацию",
                    callback_data="preview:submit",
                    style="success",
                )
            ],
            [InlineKeyboardButton(text="✏️ Загрузить пост заново", callback_data="preview:redo")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="order:cancel", style="danger")],
        ]
    )


def moderation_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Одобрить",
                    callback_data=f"mod:approve:{order_id}",
                    style="success",
                ),
                InlineKeyboardButton(
                    text="✏️ Исправить",
                    callback_data=f"mod:revision:{order_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"mod:reject:{order_id}",
                    style="danger",
                )
            ],
        ]
    )


def activation_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Активировать рекламу",
                    callback_data=f"mod:activate:{order_id}",
                    style="success",
                )
            ]
        ]
    )


def test_payment_keyboard(order_id: int, kind: str, amount: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🧪 Тестовая оплата {amount} ₽",
                    callback_data=f"testpay:{order_id}:{kind}",
                    style="success",
                )
            ]
        ]
    )


def admin_management_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Добавить", callback_data="admin:add", style="success"),
                InlineKeyboardButton(text="➖ Удалить", callback_data="admin:remove", style="danger"),
            ]
        ]
    )


def best_buttons(buttons: list[dict[str, str]]) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=button["text"], url=button["url"], style="primary")]
            for button in buttons
        ]
    )
