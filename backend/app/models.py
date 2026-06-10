"""SQLAlchemy ORM models matching the spec's database schema.

Tables: users, tracked_items, price_history.
"""
from __future__ import annotations

import enum
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TrackStatus(str, enum.Enum):
    ACTIVE = "Active"
    TRIGGERED = "Triggered"  # a qualifying price drop was detected
    EXPIRED = "Expired"      # dates have passed / tracking stopped


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tracked_items: Mapped[list[TrackedItem]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class TrackedItem(Base):
    __tablename__ = "tracked_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    provider: Mapped[str] = mapped_column(String(50), nullable=False)  # 'booking', 'travelist', ...
    raw_url: Mapped[str] = mapped_column(Text, nullable=False)

    # Parsed search parameters
    destination: Mapped[str | None] = mapped_column(String(255))
    check_in_date: Mapped[date | None] = mapped_column()
    check_out_date: Mapped[date | None] = mapped_column()
    room_config: Mapped[str | None] = mapped_column(String(100))  # e.g. "2-adults,1-child"
    target_hotel_id_or_name: Mapped[str | None] = mapped_column(String(255))
    hotel_name: Mapped[str | None] = mapped_column(String(255))  # resolved display name from the adapter

    # Pricing
    initial_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    current_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    status: Mapped[TrackStatus] = mapped_column(default=TrackStatus.ACTIVE, index=True)

    # Availability — flips to False after repeated failed price fetches
    # (offer sold out / removed). Kept separate from `status` so we don't have to
    # migrate the status enum type on Postgres.
    available: Mapped[bool] = mapped_column(default=True)
    failed_checks: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(String(500))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="tracked_items")
    price_history: Mapped[list[PriceHistory]] = relationship(
        back_populates="tracked_item",
        cascade="all, delete-orphan",
        order_by="PriceHistory.checked_at",
    )


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    tracked_item_id: Mapped[int] = mapped_column(
        ForeignKey("tracked_items.id", ondelete="CASCADE"), index=True
    )
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    tracked_item: Mapped[TrackedItem] = relationship(back_populates="price_history")
