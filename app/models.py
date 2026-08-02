from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str] = mapped_column(String(128), default="")
    last_name: Mapped[str | None] = mapped_column(String(128))
    language_code: Mapped[str | None] = mapped_column(String(16))
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    phone: Mapped[str | None] = mapped_column(String(32))
    is_bazaar_member: Mapped[bool] = mapped_column(Boolean, default=False)
    bazaar_status: Mapped[str | None] = mapped_column(String(32))
    username_history: Mapped[list[str]] = mapped_column(JSON, default=list)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    orders: Mapped[list[AdOrder]] = relationship(back_populates="user")


class Admin(Base):
    __tablename__ = "admins"

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), default="admin")
    added_by: Mapped[int | None] = mapped_column(BigInteger)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UserBlock(Base):
    __tablename__ = "user_blocks"

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), primary_key=True)
    reason: Mapped[str] = mapped_column(String(255), default="Заблокирован администрацией")
    blocked_by: Mapped[int] = mapped_column(BigInteger)
    blocked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_by: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TariffPrice(Base):
    __tablename__ = "tariff_prices"
    __table_args__ = (UniqueConstraint("tariff_code", "duration_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tariff_code: Mapped[str] = mapped_column(String(16), index=True)
    duration_code: Mapped[str] = mapped_column(String(16), index=True)
    price_rub: Mapped[int] = mapped_column(Integer)
    duration_hours: Mapped[int] = mapped_column(Integer)


class UserPrice(Base):
    __tablename__ = "user_prices"
    __table_args__ = (UniqueConstraint("user_id", "tariff_code", "duration_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    tariff_code: Mapped[str] = mapped_column(String(16))
    duration_code: Mapped[str] = mapped_column(String(16))
    price_rub: Mapped[int] = mapped_column(Integer)
    announced_discount_percent: Mapped[int | None] = mapped_column(Integer)
    updated_by: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AdOrder(Base):
    __tablename__ = "ad_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    tariff_code: Mapped[str] = mapped_column(String(16), index=True)
    duration_code: Mapped[str] = mapped_column(String(16))
    duration_hours: Mapped[int] = mapped_column(Integer)
    price_rub: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), index=True)
    content_text: Mapped[str] = mapped_column(Text, default="")
    media: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    buttons: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    requested_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    requested_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    remaining_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payment_reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    paid_rub: Mapped[int] = mapped_column(Integer, default=0)
    moderation_card_message_id: Mapped[int | None] = mapped_column(BigInteger)
    moderated_by: Mapped[int | None] = mapped_column(BigInteger)
    moderated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_by: Mapped[int | None] = mapped_column(BigInteger)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    next_publish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    pinned_message_id: Mapped[int | None] = mapped_column(BigInteger)
    awaiting_middle_pin: Mapped[bool] = mapped_column(Boolean, default=False)
    pin_changes_used: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="orders")
    publications: Mapped[list[Publication]] = relationship(back_populates="order")
    payments: Mapped[list[Payment]] = relationship(back_populates="order")


class Publication(Base):
    __tablename__ = "publications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(Integer, ForeignKey("ad_orders.id"), index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    order: Mapped[AdOrder] = relationship(back_populates="publications")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(Integer, ForeignKey("ad_orders.id"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    amount_rub: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    external_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    order: Mapped[AdOrder] = relationship(back_populates="payments")
