from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AdOrder
from app.payment_models import PaymentTransaction
from app.payments.service import (
    create_transaction,
    mark_transaction_failed,
    register_provider_payment,
)
from app.payments.yookassa import YooKassaError, YooKassaProvider


async def create_yookassa_checkout(
    session: AsyncSession,
    order: AdOrder,
    *,
    kind: str,
    amount_rub: int,
    expires_at: datetime | None = None,
) -> PaymentTransaction:
    """Create a reusable YooKassa redirect operation without enabling UI buttons."""

    provider = YooKassaProvider()
    transaction = await create_transaction(
        session,
        order,
        provider=provider.name,
        kind=kind,
        amount_rub=amount_rub,
        expires_at=expires_at,
    )
    await session.commit()

    if transaction.provider_payment_id and transaction.confirmation_url:
        return transaction

    try:
        result = await provider.create_payment(
            transaction,
            description=f"Реклама Limit, заказ №{order.id}",
        )
    except YooKassaError as error:
        await mark_transaction_failed(
            session,
            transaction,
            error_code="provider_create_failed",
            payload={"error": str(error)},
        )
        raise

    confirmation = result.get("confirmation") or {}
    await register_provider_payment(
        session,
        transaction,
        provider_payment_id=str(result["id"]),
        confirmation_url=confirmation.get("confirmation_url"),
        status=str(result.get("status") or "pending"),
        payload=result,
    )
    return transaction
