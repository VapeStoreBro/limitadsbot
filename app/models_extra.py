from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class OrderDecision(Base):
    __tablename__ = "order_decisions"

    order_id: Mapped[int] = mapped_column(
        ForeignKey("ad_orders.id"),
        primary_key=True,
    )
    action: Mapped[str] = mapped_column(String(24))
    comment: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[int] = mapped_column(BigInteger)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
