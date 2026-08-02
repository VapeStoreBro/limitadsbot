from __future__ import annotations

import math
from datetime import datetime, timezone
from html import escape

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from sqlalchemy import select

from app.config import get_settings
from app.db.session import SessionFactory
from app.enums import OrderStatus
from app.models import AdOrder
from app.payment_models import PaymentTransaction
from app.payments.service import (
    apply_succeeded_transaction,
    create_transaction,
    mark_transaction_canceled,
)
from app.services.app_settings import get_card_payment_text, get_stars_rub_per_star
from app.services.order_cards import update_buyer_card
from app.services.orders import deposit_amount
from app.services.ui_screen import render_user_screen, send_ephemeral_notice
from app.services.users import is_admin

router = Router(name="payment_methods_v9")
settings = get_settings()
PAYABLE_STATUSES = {
    OrderStatus.AWAITING_PAYMENT.value,
    OrderStatus.AWAITING_DEPOSIT.value,
    OrderStatus.BOOKED.value,
}


def payment_amount(order: AdOrder, kind: str) -> int:
    if kind == "deposit":
        return max(0, deposit_amount(order.price_rub) - order.paid_rub)
    if kind in {"full", "remainder"}:
        return max(0, order.price_rub - order.paid_rub)
    raise ValueError("Неизвестный этап оплаты")


def payment_kind_name(kind: str) -> str:
    return {
        "deposit": "предоплата",
        "remainder": "остаток",
        "full": "полная оплата",
    }.get(kind, kind)


def payment_choice_keyboard(order_id: int, kind: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⭐ Telegram Stars",
                    callback_data=f"payv9:stars:{order_id}:{kind}",
                    style="success",
                ),
                InlineKeyboardButton(
                    text="💳 Карта",
                    callback_data=f"payv9:card:{order_id}:{kind}",
                    style="primary",
                ),
            ],
            [InlineKeyboardButton(text="⬅️ К заказу", callback_data=f"buyerorder:view:{order_id}")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
        ]
    )


async def get_owned_payable_order(user_id: int, order_id: int) -> AdOrder | None:
    async with SessionFactory() as session:
        return await session.scalar(
            select(AdOrder).where(
                AdOrder.id == order_id,
                AdOrder.user_id == user_id,
            )
        )


@router.callback_query(F.data.startswith("payv9:choose:"))
async def choose_payment_method(callback: CallbackQuery) -> None:
    _, _, raw_id, kind = callback.data.split(":", 3)
    order_id = int(raw_id)
    order = await get_owned_payable_order(callback.from_user.id, order_id)
    if not order or order.status not in PAYABLE_STATUSES:
        await callback.answer("Этот заказ сейчас нельзя оплатить.", show_alert=True)
        return
    amount = payment_amount(order, kind)
    if amount <= 0:
        await callback.answer("Этот этап уже оплачен.", show_alert=True)
        return
    await render_user_screen(
        callback.bot,
        callback.from_user.id,
        f"<b><u>💰 ОПЛАТА ЗАКАЗА №{order.id}</u></b>\n\n"
        f"├ Этап: <b>{payment_kind_name(kind)}</b>\n"
        f"└ Сумма: <b>{amount} ₽</b>\n\n"
        "Выберите способ оплаты. Повторное нажатие не создаст двойное начисление.",
        payment_choice_keyboard(order.id, kind),
        source_message=callback.message,
        media_key="payment",
        text_only=True,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("payv9:card:"))
async def show_card_payment(callback: CallbackQuery) -> None:
    _, _, raw_id, kind = callback.data.split(":", 3)
    order_id = int(raw_id)
    async with SessionFactory() as session:
        order = await session.get(AdOrder, order_id)
        if not order or order.user_id != callback.from_user.id:
            await callback.answer("Заказ не найден.", show_alert=True)
            return
        amount = payment_amount(order, kind)
        if amount <= 0:
            await callback.answer("Этот этап уже оплачен.", show_alert=True)
            return
        template = await get_card_payment_text(session)
    try:
        text = template.format(
            order_id=order.id,
            amount=amount,
            kind=payment_kind_name(kind),
            user_id=callback.from_user.id,
        )
    except (KeyError, ValueError):
        text = (
            template
            + f"\n\nЗаказ: <code>№{order.id}</code>\nК оплате: <b>{amount} ₽</b>"
        )
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Я оплатил — отправить на проверку",
                    callback_data=f"payv9:card_claim:{order.id}:{kind}",
                    style="success",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Способы оплаты", callback_data=f"payv9:choose:{order.id}:{kind}")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
        ]
    )
    await render_user_screen(
        callback.bot,
        callback.from_user.id,
        text,
        markup,
        source_message=callback.message,
        media_key="payment",
        text_only=True,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("payv9:card_claim:"))
async def claim_card_payment(callback: CallbackQuery, bot: Bot) -> None:
    _, _, raw_id, kind = callback.data.split(":", 3)
    order_id = int(raw_id)
    async with SessionFactory() as session:
        order = await session.scalar(
            select(AdOrder).where(AdOrder.id == order_id).with_for_update()
        )
        if not order or order.user_id != callback.from_user.id:
            await callback.answer("Заказ не найден.", show_alert=True)
            return
        amount = payment_amount(order, kind)
        if amount <= 0:
            await callback.answer("Этот этап уже оплачен.", show_alert=True)
            return
        transaction = await create_transaction(
            session,
            order,
            provider="manual_card",
            kind=kind,
            amount_rub=amount,
        )
        already_notified = bool((transaction.provider_payload or {}).get("claim_notified"))
        transaction.status = "pending"
        transaction.provider_payload = {
            **(transaction.provider_payload or {}),
            "claim_notified": True,
            "claimed_at": datetime.now(timezone.utc).isoformat(),
        }
        transaction.updated_at = datetime.now(timezone.utc)
        await session.commit()

    if not already_notified:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Подтвердить поступление",
                        callback_data=f"payadminv9:approve:{transaction.transaction_id}",
                        style="success",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ Платёж не найден",
                        callback_data=f"payadminv9:reject:{transaction.transaction_id}",
                        style="danger",
                    )
                ],
            ]
        )
        await bot.send_message(
            settings.owner_id,
            f"<b>💳 Проверка перевода</b>\n\n"
            f"├ Заказ: <code>№{order.id}</code>\n"
            f"├ Покупатель: <code>{order.user_id}</code>\n"
            f"├ Этап: <b>{payment_kind_name(kind)}</b>\n"
            f"└ Сумма: <b>{amount} ₽</b>\n\n"
            "Проверьте поступление по банковским реквизитам.",
            reply_markup=markup,
        )
    await render_user_screen(
        bot,
        callback.from_user.id,
        f"<b>⏳ Перевод по заказу №{order.id} отправлен на проверку</b>\n\n"
        f"Сумма: <b>{amount} ₽</b>. После подтверждения карточка заказа обновится автоматически.",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ К заказу", callback_data=f"buyerorder:view:{order.id}")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
            ]
        ),
        source_message=callback.message,
        media_key="payment",
        text_only=True,
    )
    await callback.answer("Отправлено на проверку")


async def admin_allowed(callback: CallbackQuery) -> bool:
    if not callback.message or callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer("Подтверждение доступно только в личке бота.", show_alert=True)
        return False
    async with SessionFactory() as session:
        allowed = await is_admin(session, callback.from_user.id)
    if not allowed:
        await callback.answer("Доступ запрещён.", show_alert=True)
    return allowed


@router.callback_query(F.data.startswith("payadminv9:approve:"))
async def approve_card_payment(callback: CallbackQuery, bot: Bot) -> None:
    if not await admin_allowed(callback):
        return
    transaction_id = callback.data.rsplit(":", 1)[1]
    async with SessionFactory() as session:
        transaction = await session.scalar(
            select(PaymentTransaction)
            .where(PaymentTransaction.transaction_id == transaction_id)
            .with_for_update()
        )
        if not transaction or transaction.provider != "manual_card":
            await callback.answer("Транзакция не найдена.", show_alert=True)
            return
        if transaction.status == "succeeded":
            await callback.answer("Этот платёж уже подтверждён.", show_alert=True)
            return
        applied = await apply_succeeded_transaction(
            session,
            bot,
            transaction.transaction_id,
            provider_payment_id=f"manual-{transaction.transaction_id}",
            payload={"approved_by": callback.from_user.id},
        )
    await callback.message.edit_text(
        f"<b>✅ Перевод подтверждён</b>\n\nТранзакция: <code>{escape(transaction_id)}</code>"
    )
    await callback.answer("Оплата подтверждена" if applied else "Уже обработано")
    await send_ephemeral_notice(bot, transaction.user_id, "<b>✅ Перевод подтверждён</b>", seconds=25)


@router.callback_query(F.data.startswith("payadminv9:reject:"))
async def reject_card_payment(callback: CallbackQuery, bot: Bot) -> None:
    if not await admin_allowed(callback):
        return
    transaction_id = callback.data.rsplit(":", 1)[1]
    async with SessionFactory() as session:
        transaction = await session.scalar(
            select(PaymentTransaction)
            .where(PaymentTransaction.transaction_id == transaction_id)
            .with_for_update()
        )
        if not transaction or transaction.provider != "manual_card":
            await callback.answer("Транзакция не найдена.", show_alert=True)
            return
        user_id = transaction.user_id
        await mark_transaction_canceled(
            session,
            transaction,
            payload={"rejected_by": callback.from_user.id},
        )
        order = await session.get(AdOrder, transaction.order_id)
        if order:
            await update_buyer_card(session, bot, order)
    await callback.message.edit_text(
        f"<b>❌ Перевод не подтверждён</b>\n\nТранзакция: <code>{escape(transaction_id)}</code>"
    )
    await callback.answer("Отклонено")
    await send_ephemeral_notice(
        bot,
        user_id,
        "<b>❌ Перевод не найден</b>\n\nПроверьте реквизиты и сумму, затем повторите оплату.",
        seconds=30,
    )


@router.callback_query(F.data.startswith("payv9:stars:"))
async def create_stars_invoice(callback: CallbackQuery, bot: Bot) -> None:
    _, _, raw_id, kind = callback.data.split(":", 3)
    order_id = int(raw_id)
    async with SessionFactory() as session:
        order = await session.scalar(
            select(AdOrder).where(AdOrder.id == order_id).with_for_update()
        )
        if not order or order.user_id != callback.from_user.id:
            await callback.answer("Заказ не найден.", show_alert=True)
            return
        amount_rub = payment_amount(order, kind)
        if amount_rub <= 0:
            await callback.answer("Этот этап уже оплачен.", show_alert=True)
            return
        rub_per_star = await get_stars_rub_per_star(session)
        stars_amount = max(1, math.ceil(amount_rub / rub_per_star))
        transaction = await create_transaction(
            session,
            order,
            provider="telegram_stars",
            kind=kind,
            amount_rub=amount_rub,
        )
        transaction.status = "pending"
        transaction.currency = "RUB"
        transaction.provider_payload = {
            **(transaction.provider_payload or {}),
            "stars_amount": stars_amount,
            "rub_per_star": rub_per_star,
        }
        transaction.updated_at = datetime.now(timezone.utc)
        await session.commit()

    payload = f"limitads:{transaction.transaction_id}"
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"Реклама №{order.id}",
        description=(
            f"{payment_kind_name(kind).capitalize()} рекламного размещения. "
            f"Эквивалент: {amount_rub} ₽."
        ),
        payload=payload,
        currency="XTR",
        prices=[LabeledPrice(label=f"Заказ №{order.id}", amount=stars_amount)],
        provider_token="",
    )
    await render_user_screen(
        bot,
        callback.from_user.id,
        f"<b>⭐ Счёт на {stars_amount} Stars создан</b>\n\n"
        f"Заказ: <code>№{order.id}</code>\n"
        f"Этап: <b>{payment_kind_name(kind)}</b>\n"
        f"Эквивалент: <b>{amount_rub} ₽</b>\n\n"
        "Откройте появившийся счёт и подтвердите оплату. После успешной оплаты заказ обновится автоматически.",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ К заказу", callback_data=f"buyerorder:view:{order.id}")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="nav:home")],
            ]
        ),
        source_message=callback.message,
        media_key="payment",
        text_only=True,
    )
    await callback.answer("Счёт отправлен")


@router.pre_checkout_query()
async def stars_pre_checkout(query: PreCheckoutQuery) -> None:
    payload = query.invoice_payload or ""
    if not payload.startswith("limitads:"):
        await query.answer(ok=False, error_message="Неизвестный платёж.")
        return
    transaction_id = payload.split(":", 1)[1]
    async with SessionFactory() as session:
        transaction = await session.scalar(
            select(PaymentTransaction).where(
                PaymentTransaction.transaction_id == transaction_id,
                PaymentTransaction.provider == "telegram_stars",
                PaymentTransaction.user_id == query.from_user.id,
            )
        )
    expected = int((transaction.provider_payload or {}).get("stars_amount", 0)) if transaction else 0
    if not transaction or transaction.status == "succeeded":
        await query.answer(ok=False, error_message="Счёт уже обработан или не найден.")
        return
    if query.currency != "XTR" or query.total_amount != expected:
        await query.answer(ok=False, error_message="Сумма счёта изменилась. Создайте новый счёт.")
        return
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def stars_successful_payment(message: Message, bot: Bot) -> None:
    payment = message.successful_payment
    if not payment or payment.currency != "XTR" or not payment.invoice_payload.startswith("limitads:"):
        return
    transaction_id = payment.invoice_payload.split(":", 1)[1]
    async with SessionFactory() as session:
        transaction = await session.scalar(
            select(PaymentTransaction).where(
                PaymentTransaction.transaction_id == transaction_id,
                PaymentTransaction.provider == "telegram_stars",
                PaymentTransaction.user_id == message.from_user.id,
            )
        )
        if not transaction:
            await send_ephemeral_notice(bot, message.from_user.id, "Платёж получен, но заказ не найден.")
            return
        expected = int((transaction.provider_payload or {}).get("stars_amount", 0))
        if payment.total_amount != expected:
            await send_ephemeral_notice(bot, settings.owner_id, f"⚠️ Несовпадение Stars по {transaction_id}")
            return
        applied = await apply_succeeded_transaction(
            session,
            bot,
            transaction_id,
            provider_payment_id=payment.telegram_payment_charge_id,
            payload={
                "currency": payment.currency,
                "total_amount": payment.total_amount,
                "telegram_payment_charge_id": payment.telegram_payment_charge_id,
                "provider_payment_charge_id": payment.provider_payment_charge_id,
            },
        )
    await send_ephemeral_notice(
        bot,
        message.from_user.id,
        "<b>✅ Оплата Telegram Stars принята</b>" if applied else "<b>✅ Оплата уже учтена</b>",
        seconds=25,
    )


@router.message(Command("paysupport"))
async def payment_support(message: Message) -> None:
    await message.answer(
        "<b>Поддержка по оплате</b>\n\n"
        "Укажите номер заказа и способ оплаты. Для возврата Stars понадобится идентификатор платежа из квитанции Telegram."
    )