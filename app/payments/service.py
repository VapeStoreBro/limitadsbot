from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import OrderStatus
from app.models import AdOrder, BookingOffer, Payment
from app.payment_models import PaymentTransaction
from app.services.lifecycle import auto_activate_paid_order
from app.services.order_cards import update_buyer_card
from app.services.orders import deposit_amount

OPEN_STATUSES = {"created", "pending"}


def rubles_to_minor(amount_rub: int) -> int:
    if amount_rub <= 0:
        raise ValueError("Payment amount must be positive")
    return amount_rub * 100


def minor_to_rubles(amount_minor: int) -> int:
    if amount_minor <= 0 or amount_minor % 100:
        raise ValueError("Only whole-ruble transactions are supported by current tariffs")
    return amount_minor // 100


async def create_transaction(
    session: AsyncSession,
    order: AdOrder,
    *,
    provider: str,
    kind: str,
    amount_rub: int,
    expires_at: datetime | None = None,
) -> PaymentTransaction:
    """Create or reuse one unfinished operation for the same payment step."""

    amount_minor = rubles_to_minor(amount_rub)
    now = datetime.now(timezone.utc)
    existing = await session.scalar(
        select(PaymentTransaction)
        .where(
            PaymentTransaction.order_id == order.id,
            PaymentTransaction.user_id == order.user_id,
            PaymentTransaction.provider == provider,
            PaymentTransaction.kind == kind,
            PaymentTransaction.amount_minor == amount_minor,
            PaymentTransaction.status.in_(OPEN_STATUSES),
        )
        .order_by(PaymentTransaction.id.desc())
    )
    if existing and (existing.expires_at is None or existing.expires_at > now):
        return existing

    transaction = PaymentTransaction(
        transaction_id=str(uuid4()),
        order_id=order.id,
        user_id=order.user_id,
        provider=provider,
        kind=kind,
        amount_minor=amount_minor,
        currency="RUB",
        status="created",
        idempotency_key=str(uuid4()),
        provider_payment_id=None,
        confirmation_url=None,
        provider_payload={},
        error_code=None,
        created_at=now,
        updated_at=now,
        expires_at=expires_at,
        paid_at=None,
        canceled_at=None,
    )
    session.add(transaction)
    await session.flush()
    return transaction


async def register_provider_payment(
    session: AsyncSession,
    transaction: PaymentTransaction,
    *,
    provider_payment_id: str,
    confirmation_url: str | None,
    status: str,
    payload: dict,
) -> None:
    transaction.provider_payment_id = provider_payment_id
    transaction.confirmation_url = confirmation_url
    transaction.status = status
    transaction.provider_payload = payload
    transaction.updated_at = datetime.now(timezone.utc)
    await session.commit()


async def mark_transaction_failed(
    session: AsyncSession,
    transaction: PaymentTransaction,
    *,
    error_code: str,
    payload: dict | None = None,
) -> None:
    transaction.status = "failed"
    transaction.error_code = error_code[:64]
    transaction.provider_payload = payload or transaction.provider_payload or {}
    transaction.updated_at = datetime.now(timezone.utc)
    await session.commit()


async def mark_transaction_canceled(
    session: AsyncSession,
    transaction: PaymentTransaction,
    *,
    payload: dict | None = None,
) -> bool:
    if transaction.status == "succeeded":
        return False
    now = datetime.now(timezone.utc)
    transaction.status = "canceled"
    transaction.canceled_at = now
    transaction.updated_at = now
    transaction.provider_payload = payload or transaction.provider_payload or {}
    await session.commit()
    return True


async def apply_succeeded_transaction(
    session: AsyncSession,
    bot: Bot,
    transaction_id: str,
    *,
    provider_payment_id: str | None = None,
    payload: dict | None = None,
) -> bool:
    """Apply one successful provider operation exactly once."""

    transaction = await session.scalar(
        select(PaymentTransaction)
        .where(PaymentTransaction.transaction_id == transaction_id)
        .with_for_update()
    )
    if not transaction:
        raise LookupError("Payment transaction not found")
    if transaction.status == "succeeded":
        return False

    order = await session.scalar(
        select(AdOrder).where(AdOrder.id == transaction.order_id).with_for_update()
    )
    if not order or order.user_id != transaction.user_id:
        await mark_transaction_failed(
            session,
            transaction,
            error_code="order_mismatch",
            payload=payload,
        )
        raise LookupError("Order for payment transaction not found")

    amount_rub = minor_to_rubles(transaction.amount_minor)
    if transaction.kind == "deposit":
        target_paid = deposit_amount(order.price_rub)
        credited = max(0, min(amount_rub, target_paid - order.paid_rub))
    elif transaction.kind in {"remainder", "full"}:
        target_paid = order.price_rub
        credited = max(0, min(amount_rub, target_paid - order.paid_rub))
    else:
        await mark_transaction_failed(
            session,
            transaction,
            error_code="unknown_payment_kind",
            payload=payload,
        )
        raise ValueError("Unknown payment transaction kind")

    now = datetime.now(timezone.utc)
    order.paid_rub = min(order.price_rub, order.paid_rub + credited)
    order.updated_at = now

    if order.paid_rub >= order.price_rub:
        order.status = OrderStatus.READY.value
        order.remaining_due_at = None
        order.payment_reminder_sent = True
        offer = await session.get(BookingOffer, order.id)
        if offer:
            await session.delete(offer)
    elif transaction.kind == "deposit":
        # The remainder timer starts only when the reserved place is actually
        # offered for final purchase, not when the deposit is paid.
        order.status = OrderStatus.BOOKED.value
        order.remaining_due_at = None
        order.payment_reminder_sent = False

    transaction.status = "succeeded"
    transaction.provider_payment_id = (
        provider_payment_id
        or transaction.provider_payment_id
        or transaction.transaction_id
    )
    transaction.provider_payload = payload or transaction.provider_payload or {}
    transaction.paid_at = now
    transaction.updated_at = now
    transaction.error_code = None

    session.add(
        Payment(
            order_id=order.id,
            provider=transaction.provider,
            amount_rub=amount_rub,
            status="paid",
            external_id=transaction.provider_payment_id,
            created_at=transaction.created_at,
            paid_at=now,
        )
    )
    await session.commit()

    await update_buyer_card(session, bot, order)
    if order.paid_rub >= order.price_rub:
        await auto_activate_paid_order(session, bot, order)
    return True
