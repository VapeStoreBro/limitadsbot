from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from aiohttp import web
from aiogram import Bot
from sqlalchemy import select

from app.config import get_settings
from app.db.session import SessionFactory
from app.payment_models import PaymentTransaction, PaymentWebhookEvent
from app.payments.service import (
    apply_succeeded_transaction,
    mark_transaction_canceled,
)
from app.payments.yookassa import YooKassaError, YooKassaProvider

settings = get_settings()


def _amount_minor(payment: dict) -> int:
    try:
        value = Decimal(str(payment["amount"]["value"]))
    except (KeyError, InvalidOperation, TypeError) as error:
        raise ValueError("Provider payment has invalid amount") from error
    return int(value * 100)


async def handle_yookassa_webhook(request: web.Request, bot: Bot) -> web.Response:
    """Verify the notification against YooKassa API, then apply it once."""

    if not settings.yookassa_configured:
        return web.json_response({"ok": False, "error": "provider_disabled"}, status=503)

    try:
        notification = await request.json()
        event_type = str(notification["event"])
        provider_payment_id = str(notification["object"]["id"])
    except Exception:
        return web.json_response({"ok": False, "error": "invalid_payload"}, status=400)

    provider = YooKassaProvider()
    try:
        payment = await provider.retrieve_payment(provider_payment_id)
    except YooKassaError:
        return web.json_response({"ok": False, "error": "verification_failed"}, status=502)

    metadata = payment.get("metadata") or {}
    transaction_id = str(metadata.get("transaction_id") or "")
    provider_status = str(payment.get("status") or "")
    event_key = f"{event_type}:{provider_payment_id}:{provider_status}"
    now = datetime.now(timezone.utc)

    async with SessionFactory() as session:
        duplicate = await session.scalar(
            select(PaymentWebhookEvent).where(
                PaymentWebhookEvent.provider == provider.name,
                PaymentWebhookEvent.event_key == event_key,
            )
        )
        if duplicate:
            return web.json_response({"ok": True, "duplicate": True})

        event = PaymentWebhookEvent(
            provider=provider.name,
            event_key=event_key,
            event_type=event_type,
            provider_payment_id=provider_payment_id,
            payload=notification,
            status="received",
            received_at=now,
            processed_at=None,
            error_text=None,
        )
        session.add(event)
        await session.commit()

        transaction = await session.scalar(
            select(PaymentTransaction).where(
                PaymentTransaction.transaction_id == transaction_id
            )
        )
        if not transaction or transaction.provider != provider.name:
            event.status = "ignored"
            event.error_text = "transaction_not_found"
            event.processed_at = now
            await session.commit()
            return web.json_response({"ok": True, "ignored": True})

        valid = (
            transaction.provider_payment_id in {None, provider_payment_id}
            and transaction.amount_minor == _amount_minor(payment)
            and transaction.currency == str(payment.get("amount", {}).get("currency") or "")
            and str(metadata.get("order_id") or "") == str(transaction.order_id)
            and str(metadata.get("user_id") or "") == str(transaction.user_id)
            and str(metadata.get("kind") or "") == transaction.kind
        )
        if not valid:
            event.status = "error"
            event.error_text = "provider_data_mismatch"
            event.processed_at = now
            await session.commit()
            return web.json_response({"ok": False, "error": "data_mismatch"}, status=409)

        transaction.provider_payment_id = provider_payment_id
        transaction.provider_payload = payment
        transaction.updated_at = now
        await session.commit()

        if provider_status == "succeeded" and bool(payment.get("paid")):
            await apply_succeeded_transaction(
                session,
                bot,
                transaction.transaction_id,
                provider_payment_id=provider_payment_id,
                payload=payment,
            )
        elif provider_status == "canceled":
            await mark_transaction_canceled(session, transaction, payload=payment)
        else:
            transaction.status = provider_status or "pending"
            transaction.provider_payload = payment
            transaction.updated_at = now
            await session.commit()

        event.status = "processed"
        event.processed_at = datetime.now(timezone.utc)
        await session.commit()

    return web.json_response({"ok": True})


async def payment_return(_: web.Request) -> web.Response:
    return web.Response(
        text=(
            "Оплата передана на проверку. Вернитесь в Telegram — "
            "карточка заказа обновится автоматически."
        ),
        content_type="text/plain",
        charset="utf-8",
    )
