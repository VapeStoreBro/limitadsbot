from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.enums import OrderStatus, TariffCode
from app.keyboards import DURATION_NAMES, TARIFF_NAMES
from app.models import AdOrder, MiddlePinCandidate, OrderCard

settings = get_settings()
BUYER_CARD = "buyer"

STATUS_TEXT = {
    OrderStatus.MODERATION.value: "🛡 Пост проверяет администрация",
    OrderStatus.REVISION.value: "✏️ Пост нужно исправить",
    OrderStatus.REJECTED.value: "❌ Заявка отклонена",
    OrderStatus.AWAITING_PAYMENT.value: "💳 Одобрено — ожидается полная оплата",
    OrderStatus.AWAITING_DEPOSIT.value: "💳 Одобрено — ожидается предоплата 50%",
    OrderStatus.BOOKED.value: "📅 Место забронировано",
    OrderStatus.READY.value: "⚙️ Оплачено — автоматический запуск готовится",
    OrderStatus.ACTIVE.value: "✅ Реклама активна",
    OrderStatus.COMPLETED.value: "🏁 Рекламный период завершён",
    OrderStatus.CANCELLED.value: "🚫 Заказ отменён",
}


def _date(value: datetime | None) -> str:
    if not value:
        return "—"
    return value.astimezone(ZoneInfo(settings.timezone)).strftime("%d.%m.%Y %H:%M")


def _button_by_kind(order: AdOrder, kind: str) -> dict[str, str] | None:
    for button in order.buttons or []:
        if button.get("kind") == kind:
            return button
    return None


def render_buyer_card(order: AdOrder, candidate: MiddlePinCandidate | None = None) -> str:
    contact = _button_by_kind(order, "contact")
    resource = _button_by_kind(order, "resource")
    lines = [
        f"<b><u>📦 РЕКЛАМА №{order.id}</u></b>",
        "",
        f"<b>Статус:</b> {STATUS_TEXT.get(order.status, escape(order.status))}",
        "",
        f"├ Тариф: <b>{TARIFF_NAMES.get(order.tariff_code, order.tariff_code)}</b>",
        f"├ Срок: <b>{DURATION_NAMES.get(order.duration_code, order.duration_code)}</b>",
        f"├ Стоимость: <b>{order.price_rub} ₽</b>",
        f"├ Оплачено: <b>{order.paid_rub} ₽</b>",
        f"├ Фотографий: <b>{len(order.media or [])}/8</b>",
    ]
    if order.tariff_code == TariffCode.BEST.value:
        lines.extend(
            [
                f"├ Контакт: <b>{'✅ добавлен' if contact else 'не добавлен'}</b>",
                f"├ Ресурс: <b>{'✅ добавлен' if resource else 'не добавлен'}</b>",
            ]
        )
    if order.tariff_code == TariffCode.MIDDLE.value:
        if candidate:
            pin_status = "🟡 найден новый пост — подтвердите замену"
        elif order.awaiting_middle_pin and order.pinned_message_id:
            pin_status = "ожидается новый рекламный пост в барахолке"
        elif order.awaiting_middle_pin:
            pin_status = "ожидается первый рекламный пост в барахолке"
        elif order.pinned_message_id:
            pin_status = "✅ установлен"
        else:
            pin_status = "не установлен"
        lines.extend(
            [
                f"├ Закреп: <b>{pin_status}</b>",
                f"├ Использовано замен: <b>{order.pin_changes_used}/2</b>",
            ]
        )
    lines.extend(
        [
            f"├ Запуск: <code>{_date(order.activated_at or order.requested_start_at)}</code>",
            f"└ Окончание: <code>{_date(order.ends_at or order.requested_end_at)}</code>",
        ]
    )
    if candidate:
        lines.extend(
            [
                "",
                "<b>Найден новый пост для закрепа:</b>",
                f"<i>{escape(candidate.preview_text or 'пост с фотографией')}</i>",
                "Подтвердите его кнопкой ниже. До подтверждения старый закреп не меняется.",
            ]
        )
    if order.status == OrderStatus.ACTIVE.value:
        lines.extend(["", "<i>Все изменения выполняются через кнопки этой карточки.</i>"])
    return "\n".join(lines)


def buyer_card_keyboard(order: AdOrder, candidate: MiddlePinCandidate | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if order.status == OrderStatus.AWAITING_PAYMENT.value:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🧪 Оплатить {max(0, order.price_rub - order.paid_rub)} ₽",
                    callback_data=f"testpay:{order.id}:full",
                    style="success",
                )
            ]
        )
    elif order.status == OrderStatus.AWAITING_DEPOSIT.value:
        amount = max(1, order.price_rub // 2)
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🧪 Внести предоплату {amount} ₽",
                    callback_data=f"testpay:{order.id}:deposit",
                    style="success",
                )
            ]
        )
    elif order.status == OrderStatus.BOOKED.value and order.paid_rub < order.price_rub:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🧪 Доплатить {order.price_rub - order.paid_rub} ₽",
                    callback_data=f"testpay:{order.id}:remainder",
                    style="success",
                )
            ]
        )

    if order.status == OrderStatus.ACTIVE.value:
        rows.append(
            [
                InlineKeyboardButton(
                    text="👁 Показать рекламный пост",
                    callback_data=f"buyerorder:show:{order.id}",
                    style="primary",
                )
            ]
        )
        if order.tariff_code == TariffCode.BEST.value:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="✏️ Изменить пост по частям",
                        callback_data=f"bestedit:menu:{order.id}",
                        style="success",
                    )
                ]
            )
        elif order.tariff_code == TariffCode.MIDDLE.value:
            if candidate:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text="✅ Закрепить найденный пост",
                            callback_data=f"middlepin:confirm:{order.id}",
                            style="success",
                        ),
                        InlineKeyboardButton(
                            text="❌ Не этот",
                            callback_data=f"middlepin:reject:{order.id}",
                            style="danger",
                        ),
                    ]
                )
            elif order.pinned_message_id and order.pin_changes_used < 2:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=f"📌 Сменить закреп · осталось {2 - order.pin_changes_used}",
                            callback_data=f"buyerorder:pin:{order.id}",
                            style="success",
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
            if order.awaiting_middle_pin and not candidate:
                rows.append(
                    [
                        InlineKeyboardButton(
                            text="❌ Отменить ожидание поста",
                            callback_data=f"buyerorder:pin_cancel:{order.id}",
                            style="danger",
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
    elif order.tariff_code == TariffCode.BEST.value and order.status not in {
        OrderStatus.COMPLETED.value,
        OrderStatus.CANCELLED.value,
        OrderStatus.REJECTED.value,
    }:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✏️ Изменить Best по частям",
                    callback_data=f"bestedit:menu:{order.id}",
                    style="primary",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(text="⬅️ Мои рекламы", callback_data="profile:orders"),
            InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def get_candidate(session: AsyncSession, order_id: int) -> MiddlePinCandidate | None:
    return await session.get(MiddlePinCandidate, order_id)


async def register_buyer_card(
    session: AsyncSession,
    order: AdOrder,
    chat_id: int,
    message_id: int,
) -> OrderCard:
    now = datetime.now(timezone.utc)
    card = await session.scalar(
        select(OrderCard).where(
            OrderCard.order_id == order.id,
            OrderCard.kind == BUYER_CARD,
            OrderCard.chat_id == chat_id,
        )
    )
    if card is None:
        card = OrderCard(
            order_id=order.id,
            kind=BUYER_CARD,
            chat_id=chat_id,
            message_id=message_id,
            created_at=now,
            updated_at=now,
        )
        session.add(card)
    else:
        card.message_id = message_id
        card.updated_at = now
    await session.commit()
    return card


async def _edit_existing(
    bot: Bot,
    card: OrderCard,
    text: str,
    markup: InlineKeyboardMarkup,
) -> bool:
    try:
        await bot.edit_message_text(
            chat_id=card.chat_id,
            message_id=card.message_id,
            text=text,
            reply_markup=markup,
        )
        return True
    except TelegramBadRequest as error:
        if "message is not modified" in str(error).lower():
            return True
        try:
            await bot.edit_message_caption(
                chat_id=card.chat_id,
                message_id=card.message_id,
                caption=text,
                reply_markup=markup,
            )
            return True
        except Exception:
            return False
    except Exception:
        return False


async def update_buyer_card(
    session: AsyncSession,
    bot: Bot,
    order: AdOrder,
    *,
    source_message: Message | None = None,
) -> OrderCard:
    candidate = await get_candidate(session, order.id)
    text = render_buyer_card(order, candidate)
    markup = buyer_card_keyboard(order, candidate)
    card = await session.scalar(
        select(OrderCard).where(
            OrderCard.order_id == order.id,
            OrderCard.kind == BUYER_CARD,
            OrderCard.chat_id == order.user_id,
        )
    )

    if source_message is not None:
        card = await register_buyer_card(
            session,
            order,
            source_message.chat.id,
            source_message.message_id,
        )

    if card and await _edit_existing(bot, card, text, markup):
        card.updated_at = datetime.now(timezone.utc)
        await session.commit()
        return card

    message = await bot.send_message(order.user_id, text, reply_markup=markup)
    return await register_buyer_card(session, order, order.user_id, message.message_id)


async def update_staff_card(
    session: AsyncSession,
    bot: Bot,
    order: AdOrder,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    if not order.moderation_card_message_id:
        return
    from app.services.app_settings import get_staff_chat_id

    staff_chat_id = await get_staff_chat_id(session)
    try:
        await bot.edit_message_text(
            chat_id=staff_chat_id,
            message_id=order.moderation_card_message_id,
            text=text,
            reply_markup=reply_markup,
        )
    except Exception:
        pass
