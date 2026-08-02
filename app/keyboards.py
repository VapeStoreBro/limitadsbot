from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from app.enums import DurationCode, TariffCode

TARIFF_NAMES = {
    TariffCode.STANDARD.value: "1. Standard 🎪",
    TariffCode.MIDDLE.value: "2. Middle 🎭",
    TariffCode.BEST.value: "3. Best 🔥",
}
DURATION_NAMES = {
    DurationCode.DAY.value: "День",
    DurationCode.WEEK.value: "Неделя",
    DurationCode.MONTH.value: "Месяц",
}


def customer_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="📢 Купить рекламу"), KeyboardButton(text="📦 Мои заказы")],
        [KeyboardButton(text="📱 Поделиться номером", request_contact=True), KeyboardButton(text="ℹ️ Тарифы")],
    ]
    if is_admin:
        rows.append([KeyboardButton(text="🔐 Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def admin_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📥 Новые заявки"), KeyboardButton(text="📢 Активная реклама")],
        [KeyboardButton(text="📅 Бронирования"), KeyboardButton(text="👥 Клиенты")],
        [KeyboardButton(text="💰 Персональные цены"), KeyboardButton(text="🏷 Тарифы")],
        [KeyboardButton(text="👮 Администраторы"), KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text="⬅️ Меню покупателя")],
    ], resize_keyboard=True)


def tariffs_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=name, callback_data=f"tariff:{code}")]
        for code, name in TARIFF_NAMES.items()
    ])


def durations_keyboard(tariff_code: str, prices: dict[str, int]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{DURATION_NAMES[duration]} — {prices[duration]} ₽",
            callback_data=f"duration:{tariff_code}:{duration}",
        )]
        for duration in (DurationCode.DAY.value, DurationCode.WEEK.value, DurationCode.MONTH.value)
    ])


def start_mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Запуск после проверки", callback_data="startmode:now")],
        [InlineKeyboardButton(text="📅 Забронировать дату (50%)", callback_data="startmode:book")],
    ])


def moderation_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"mod:approve:{order_id}"),
            InlineKeyboardButton(text="✏️ Исправить", callback_data=f"mod:revision:{order_id}"),
        ],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"mod:reject:{order_id}")],
    ])


def activation_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Активировать рекламу", callback_data=f"mod:activate:{order_id}")]
    ])


def test_payment_keyboard(order_id: int, kind: str, amount: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🧪 Тестовая оплата {amount} ₽", callback_data=f"testpay:{order_id}:{kind}")]
    ])


def best_buttons(buttons: list[dict[str, str]]) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=button["text"], url=button["url"])] for button in buttons
    ])
