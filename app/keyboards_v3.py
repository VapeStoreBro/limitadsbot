from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.enums import TariffCode


LAUNCH_TEXT = "🚀 Открыть бота"

STATUS_SHORT = {
    "moderation": "На проверке",
    "revision": "Нужно исправить",
    "rejected": "Отклонена",
    "awaiting_payment": "Ждёт оплату",
    "awaiting_deposit": "Ждёт предоплату",
    "booked": "Забронирована",
    "ready": "Оплачена — ждёт запуска",
    "active": "Активна",
    "completed": "Завершена",
    "cancelled": "Отменена",
}

TARIFF_SHORT = {
    "standard": "Standard",
    "middle": "Middle",
    "best": "Best",
}


def launcher_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=LAUNCH_TEXT, style="success")]],
        resize_keyboard=True,
        one_time_keyboard=True,
        is_persistent=False,
        input_field_placeholder="Откройте главное меню",
    )


def home_keyboard(back_callback: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if back_callback:
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback)])
    rows.append(
        [
            InlineKeyboardButton(
                text="🏠 Главное меню",
                callback_data="nav:home",
                style="primary",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def upload_navigation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ К выбору тарифа", callback_data="order:back_tariffs")],
            [
                InlineKeyboardButton(
                    text="🏠 Отменить и в главное меню",
                    callback_data="nav:home",
                    style="danger",
                )
            ],
        ]
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
                text="✅ Готово — предпросмотр",
                callback_data="best:preview",
                style="success",
            )
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text="⬅️ Загрузить другой пост", callback_data="preview:redo"),
            InlineKeyboardButton(text="🏠 Меню", callback_data="nav:home"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def buyer_orders_keyboard(orders: list) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for order in orders:
        status = STATUS_SHORT.get(order.status, order.status)
        tariff = TARIFF_SHORT.get(order.tariff_code, order.tariff_code)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"№{order.id} · {tariff} · {status}",
                    callback_data=f"buyerorder:view:{order.id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="🏠 Главное меню",
                callback_data="nav:home",
                style="primary",
            )
        ]
    )
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
                label = (
                    "📌 Как установить первый закреп"
                    if not order.pinned_message_id
                    else "📌 Как заменить закреп"
                )
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=label,
                            callback_data=f"buyerorder:pin_help:{order.id}",
                            style="success",
                        )
                    ]
                )
                rows.append(
                    [
                        InlineKeyboardButton(
                            text="❌ Отменить ожидание поста",
                            callback_data=f"buyerorder:pin_cancel:{order.id}",
                            style="danger",
                        )
                    ]
                )
            elif not order.pinned_message_id:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text="📌 Установить первый закреп",
                            callback_data=f"buyerorder:pin:{order.id}",
                            style="success",
                        )
                    ]
                )
            elif order.pin_changes_used < 2:
                remaining = 2 - order.pin_changes_used
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=f"📌 Сменить закреп · осталось {remaining}",
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
    rows.append(
        [
            InlineKeyboardButton(text="⬅️ К моим рекламам", callback_data="profile:orders"),
            InlineKeyboardButton(text="🏠 Меню", callback_data="nav:home"),
        ]
    )
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
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
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
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
        ]
    )


def active_ads_keyboard(orders: list) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=(
                    f"№{order.id} · {TARIFF_SHORT.get(order.tariff_code, order.tariff_code)} · "
                    f"до {order.ends_at:%d.%m %H:%M}"
                ),
                callback_data=f"activev3:view:{order.id}",
            )
        ]
        for order in orders
    ]
    rows.append(
        [
            InlineKeyboardButton(text="⬅️ Админ-панель", callback_data="profile:admin"),
            InlineKeyboardButton(text="🏠 Меню", callback_data="nav:home"),
        ]
    )
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
    rows.append(
        [
            InlineKeyboardButton(text="⬅️ К активным", callback_data="adminv3:active"),
            InlineKeyboardButton(text="🏠 Меню", callback_data="nav:home"),
        ]
    )
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
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
        ]
    )


def moderation_reason_keyboard(action: str, order_id: int) -> InlineKeyboardMarkup:
    if action == "revision":
        choices = [
            ("📝 Исправьте текст", "text"),
            ("🔗 Исправьте ссылки", "links"),
            ("🖼 Исправьте фотографии", "photos"),
            ("💬 Свяжитесь с администрацией", "contact"),
        ]
    else:
        choices = [
            ("🚫 Нарушение правил", "rules"),
            ("📦 Не подходит для барахолки", "category"),
            ("⛔ Запрещённый товар/услуга", "forbidden"),
            ("Без комментария", "none"),
        ]
    rows = [
        [
            InlineKeyboardButton(
                text=label,
                callback_data=f"modreason:{action}:{order_id}:{code}",
            )
        ]
        for label, code in choices
    ]
    rows.append(
        [InlineKeyboardButton(text="⬅️ Назад к решению", callback_data=f"modreason:back:{order_id}:none")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def client_actions_keyboard(user_id: int, blocked: bool) -> InlineKeyboardMarkup:
    block_button = InlineKeyboardButton(
        text="✅ Разблокировать" if blocked else "🚫 Заблокировать",
        callback_data=(f"clientv4:unblock:{user_id}" if blocked else f"clientv4:block_confirm:{user_id}"),
        style="success" if blocked else "danger",
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📦 Заказы", callback_data=f"clientv4:orders:{user_id}"),
                InlineKeyboardButton(text="💰 Назначить цену", callback_data=f"pricev3:user:{user_id}"),
            ],
            [
                InlineKeyboardButton(text="✉️ Написать", url=f"tg://user?id={user_id}"),
                block_button,
            ],
            [InlineKeyboardButton(text="⬅️ К клиентам", callback_data="adminv3:clients")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
        ]
    )


def client_block_confirmation(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚫 Заблокировать и остановить рекламу",
                    callback_data=f"clientv4:block:{user_id}",
                    style="danger",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"adminv3:client:{user_id}")],
        ]
    )


def price_tariff_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Standard", callback_data="pricev3:tariff:standard"),
                InlineKeyboardButton(text="Middle", callback_data="pricev3:tariff:middle"),
                InlineKeyboardButton(text="Best", callback_data="pricev3:tariff:best"),
            ],
            [InlineKeyboardButton(text="🏠 Отмена", callback_data="nav:home")],
        ]
    )


def price_duration_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="1 день", callback_data="pricev3:duration:day")],
            [InlineKeyboardButton(text="7 дней", callback_data="pricev3:duration:week")],
            [InlineKeyboardButton(text="30 дней", callback_data="pricev3:duration:month")],
            [InlineKeyboardButton(text="🏠 Отмена", callback_data="nav:home")],
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
        [InlineKeyboardButton(text="🏠 Отмена", callback_data="nav:home")],
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
            [
                InlineKeyboardButton(text="⬅️ Админ-панель", callback_data="profile:admin"),
                InlineKeyboardButton(text="🏠 Меню", callback_data="nav:home"),
            ],
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
            ],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
        ]
    )
