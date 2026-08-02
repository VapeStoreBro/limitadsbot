from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.enums import DurationCode, TariffCode


LAUNCH_TEXT = "🚀 Открыть бота"


def launcher_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=LAUNCH_TEXT, style="success")]],
        resize_keyboard=True,
        is_persistent=True,
        selective=True,
        input_field_placeholder="Нажмите, чтобы открыть меню",
    )


def simplified_best_keyboard(button_count: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if button_count < 2:
        rows.append(
            [
                InlineKeyboardButton(
                    text="👤 Добавить контакт",
                    callback_data="bestv3:contact",
                    style="primary",
                ),
                InlineKeyboardButton(
                    text="🔗 Добавить ресурс",
                    callback_data="bestv3:resource",
                    style="primary",
                ),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="✅ Готово — посмотреть пост",
                callback_data="best:preview",
                style="success",
            )
        ]
    )
    rows.append(
        [InlineKeyboardButton(text="❌ Отмена", callback_data="order:cancel", style="danger")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def buyer_orders_keyboard(orders: list) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for order in orders:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"№{order.id} · {order.tariff_code} · {order.status}",
                    callback_data=f"buyerorder:view:{order.id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Главное меню", callback_data="profile:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def buyer_order_actions(order) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if order.status == "active":
        rows.append(
            [
                InlineKeyboardButton(
                    text="👁 Показать рекламный пост",
                    callback_data=f"buyerorder:show:{order.id}",
                    style="primary",
                )
            ]
        )
        if order.tariff_code == TariffCode.MIDDLE.value:
            if order.awaiting_middle_pin:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text="❌ Отменить смену закрепа",
                            callback_data=f"buyerorder:pin_cancel:{order.id}",
                            style="danger",
                        )
                    ]
                )
            elif order.pin_changes_used < 2:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=f"📌 Сменить закреп ({order.pin_changes_used}/2)",
                            callback_data=f"buyerorder:pin:{order.id}",
                            style="success",
                        )
                    ]
                )
        rows.append(
            [
                InlineKeyboardButton(
                    text="🛑 Завершить рекламу",
                    callback_data=f"buyerorder:stop_confirm:{order.id}",
                    style="danger",
                )
            ]
        )
    if order.status == "booked" and order.paid_rub < order.price_rub:
        remaining = order.price_rub - order.paid_rub
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"💳 Доплатить {remaining} ₽",
                    callback_data=f"testpay:{order.id}:remainder",
                    style="success",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ К моим рекламам", callback_data="profile:orders")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def buyer_stop_confirmation(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛑 Да, завершить",
                    callback_data=f"buyerorder:stop:{order_id}",
                    style="danger",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"buyerorder:view:{order_id}")],
        ]
    )


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📥 Заявки", callback_data="adminv3:orders"),
                InlineKeyboardButton(text="🚀 Активная реклама", callback_data="adminv3:active"),
            ],
            [
                InlineKeyboardButton(text="📅 Бронирования", callback_data="adminv3:bookings"),
                InlineKeyboardButton(text="👥 Клиенты", callback_data="adminv3:clients"),
            ],
            [
                InlineKeyboardButton(text="💳 Платежи", callback_data="adminv3:payments"),
                InlineKeyboardButton(text="💰 Персональные цены", callback_data="adminv3:prices"),
            ],
            [
                InlineKeyboardButton(text="🏷 Тарифы", callback_data="adminv3:tariffs"),
                InlineKeyboardButton(text="👮 Администраторы", callback_data="adminv3:admins"),
            ],
            [
                InlineKeyboardButton(text="📊 Статистика", callback_data="adminv3:stats"),
                InlineKeyboardButton(text="⚙️ Настройки", callback_data="adminv3:settings"),
            ],
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="profile:home")],
        ]
    )


def active_ads_keyboard(orders: list) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"№{order.id} · {order.tariff_code} · до {order.ends_at:%d.%m %H:%M}",
                callback_data=f"activev3:view:{order.id}",
            )
        ]
        for order in orders
    ]
    rows.append([InlineKeyboardButton(text="⬅️ Админ-панель", callback_data="profile:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def active_ad_actions(order) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="👁 Показать пост",
                callback_data=f"activev3:show:{order.id}",
                style="primary",
            )
        ],
        [
            InlineKeyboardButton(text="+1 день", callback_data=f"activev3:extend:{order.id}:24"),
            InlineKeyboardButton(text="+7 дней", callback_data=f"activev3:extend:{order.id}:168"),
            InlineKeyboardButton(text="+30 дней", callback_data=f"activev3:extend:{order.id}:720"),
        ],
    ]
    if order.pinned_message_id:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📌 Закрепить повторно",
                    callback_data=f"activev3:repin:{order.id}",
                )
            ]
        )
    if order.tariff_code == TariffCode.BEST.value:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📣 Опубликовать сейчас",
                    callback_data=f"activev3:publish:{order.id}",
                    style="success",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="🛑 Завершить сейчас",
                callback_data=f"activev3:stop_confirm:{order.id}",
                style="danger",
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="⬅️ К активным", callback_data="adminv3:active")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def active_stop_confirmation(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛑 Подтвердить завершение",
                    callback_data=f"activev3:stop:{order_id}",
                    style="danger",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"activev3:view:{order_id}")],
        ]
    )


def price_tariff_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Standard", callback_data="pricev3:tariff:standard"),
                InlineKeyboardButton(text="Middle", callback_data="pricev3:tariff:middle"),
                InlineKeyboardButton(text="Best", callback_data="pricev3:tariff:best"),
            ]
        ]
    )


def price_duration_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="1 день", callback_data="pricev3:duration:day")],
            [InlineKeyboardButton(text="7 дней", callback_data="pricev3:duration:week")],
            [InlineKeyboardButton(text="30 дней", callback_data="pricev3:duration:month")],
        ]
    )


def price_discount_keyboard() -> InlineKeyboardMarkup:
    values = (0, 5, 10, 15, 20, 25, 30)
    rows = [
        [
            InlineKeyboardButton(
                text="Без скидки" if value == 0 else f"{value}%",
                callback_data=f"pricev3:discount:{value}",
            )
            for value in values[:4]
        ],
        [
            InlineKeyboardButton(text=f"{value}%", callback_data=f"pricev3:discount:{value}")
            for value in values[4:]
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🧪 Проверить группу стафа",
                    callback_data="settingsv3:test_staff",
                    style="primary",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Изменить ID группы стафа",
                    callback_data="settingsv3:change_staff",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Админ-панель", callback_data="profile:admin")],
        ]
    )


def private_activation_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Открыть заказ и запустить",
                    callback_data=f"adminorder:view:{order_id}",
                    style="success",
                )
            ]
        ]
    )
