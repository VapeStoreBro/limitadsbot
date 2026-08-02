import asyncio
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN")

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.enums import DurationCode, OrderStatus, TariffCode
from app.models import AdOrder, Base, Payment, TariffPrice, User
from app.payment_models import PaymentTransaction  # noqa: F401 - register metadata
from app.payments import service as payment_service
from app.services.orders import create_order


async def _noop(*args, **kwargs):
    return None


async def _database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _user(now: datetime) -> User:
    return User(
        id=1001,
        username="buyer",
        first_name="Buyer",
        last_name=None,
        language_code="ru",
        is_premium=False,
        phone="+70000000000",
        is_bazaar_member=True,
        bazaar_status="member",
        username_history=["buyer"],
        first_seen_at=now,
        last_seen_at=now,
    )


def _booked_order(now: datetime) -> AdOrder:
    return AdOrder(
        user_id=1001,
        tariff_code=TariffCode.BEST.value,
        duration_code=DurationCode.DAY.value,
        duration_hours=24,
        price_rub=1000,
        status=OrderStatus.AWAITING_DEPOSIT.value,
        content_text="Тестовый рекламный пост",
        media=[],
        buttons=[],
        requested_start_at=now + timedelta(days=5),
        requested_end_at=now + timedelta(days=6),
        remaining_due_at=None,
        payment_reminder_sent=False,
        paid_rub=0,
        moderation_card_message_id=None,
        moderated_by=None,
        moderated_at=None,
        activated_by=None,
        activated_at=None,
        ends_at=None,
        next_publish_at=None,
        pinned_message_id=None,
        awaiting_middle_pin=False,
        pin_changes_used=0,
        created_at=now,
        updated_at=now,
    )


def test_deposit_is_idempotent_and_does_not_start_remainder_timer(monkeypatch):
    async def scenario():
        engine, session_factory = await _database()
        now = datetime.now(timezone.utc)
        try:
            async with session_factory() as session:
                session.add(_user(now))
                order = _booked_order(now)
                session.add(order)
                await session.commit()
                await session.refresh(order)

                transaction = await payment_service.create_transaction(
                    session,
                    order,
                    provider="test",
                    kind="deposit",
                    amount_rub=500,
                )
                await session.commit()

                monkeypatch.setattr(payment_service, "update_buyer_card", _noop)
                monkeypatch.setattr(payment_service, "auto_activate_paid_order", _noop)

                first = await payment_service.apply_succeeded_transaction(
                    session,
                    object(),
                    transaction.transaction_id,
                    provider_payment_id=f"test-{transaction.transaction_id}",
                )
                second = await payment_service.apply_succeeded_transaction(
                    session,
                    object(),
                    transaction.transaction_id,
                    provider_payment_id=f"test-{transaction.transaction_id}",
                )

                stored = await session.get(AdOrder, order.id)
                payment_count = await session.scalar(
                    select(func.count()).select_from(Payment)
                )
                assert first is True
                assert second is False
                assert stored.paid_rub == 500
                assert stored.status == OrderStatus.BOOKED.value
                assert stored.remaining_due_at is None
                assert payment_count == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_new_booking_has_no_remainder_deadline():
    async def scenario():
        engine, session_factory = await _database()
        now = datetime.now(timezone.utc)
        try:
            async with session_factory() as session:
                session.add(_user(now))
                session.add(
                    TariffPrice(
                        tariff_code=TariffCode.BEST.value,
                        duration_code=DurationCode.DAY.value,
                        price_rub=1000,
                        duration_hours=24,
                    )
                )
                await session.commit()

                order = await create_order(
                    session,
                    user_id=1001,
                    tariff_code=TariffCode.BEST.value,
                    duration_code=DurationCode.DAY.value,
                    content_text="Тестовый рекламный пост",
                    media=[],
                    buttons=[],
                    requested_start_at=now + timedelta(days=5),
                )
                assert order.remaining_due_at is None
                assert order.payment_reminder_sent is False
        finally:
            await engine.dispose()

    asyncio.run(scenario())
