from enum import Enum


class StrEnum(str, Enum):
    """Python 3.10-compatible replacement for enum.StrEnum (added in Python 3.11)."""

    def __str__(self) -> str:
        return self.value


class TariffCode(StrEnum):
    STANDARD = "standard"
    MIDDLE = "middle"
    BEST = "best"


class DurationCode(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class OrderStatus(StrEnum):
    MODERATION = "moderation"
    REVISION = "revision"
    REJECTED = "rejected"
    AWAITING_PAYMENT = "awaiting_payment"
    AWAITING_DEPOSIT = "awaiting_deposit"
    BOOKED = "booked"
    READY = "ready"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PublicationKind(StrEnum):
    MAIN = "main"
    COPY = "copy"
    MIDDLE_PIN = "middle_pin"
