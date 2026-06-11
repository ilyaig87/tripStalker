"""Database access helpers (thin layer over SQLAlchemy)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import PriceHistory, TrackedItem, TrackStatus, User
from app.url_parser import ParsedUrl


def get_or_create_user(db: Session, email: str) -> User:
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email)
        db.add(user)
        db.flush()  # assign id without committing yet
    return user


def create_track(
    db: Session,
    user: User,
    parsed: ParsedUrl,
    initial_price: Decimal | None,
    currency: str,
    hotel_name: str | None = None,
    hotel_portion: Decimal | None = None,
    flight_portion: Decimal | None = None,
) -> TrackedItem:
    item = TrackedItem(
        user_id=user.id,
        provider=parsed.provider,
        raw_url=parsed.raw_url,
        destination=parsed.destination,
        check_in_date=parsed.check_in_date,
        check_out_date=parsed.check_out_date,
        room_config=parsed.room_config,
        target_hotel_id_or_name=parsed.target_hotel_id_or_name,
        hotel_name=hotel_name,
        hotel_portion=hotel_portion,
        flight_portion=flight_portion,
        initial_price=initial_price,
        current_price=initial_price,
        currency=currency,
        status=TrackStatus.ACTIVE,
        last_checked_at=datetime.now(timezone.utc),  # creation fetched the initial price
    )
    db.add(item)
    db.flush()
    if initial_price is not None:
        db.add(
            PriceHistory(
                tracked_item_id=item.id,
                price=initial_price,
                hotel_portion=hotel_portion,
                flight_portion=flight_portion,
            )
        )
    db.commit()
    db.refresh(item)
    return item


def get_tracks_by_email(db: Session, email: str) -> list[TrackedItem]:
    return list(
        db.scalars(
            select(TrackedItem)
            .join(User)
            .where(User.email == email)
            .order_by(TrackedItem.created_at.desc())
        )
    )


def get_track(db: Session, track_id: int) -> TrackedItem | None:
    return db.scalar(
        select(TrackedItem)
        .where(TrackedItem.id == track_id)
        .options(selectinload(TrackedItem.price_history))
    )


def delete_track(db: Session, track_id: int) -> bool:
    item = db.get(TrackedItem, track_id)
    if item is None:
        return False
    db.delete(item)
    db.commit()
    return True


def reset_baseline(db: Session, track_id: int) -> TrackedItem | None:
    """Re-baseline a track: make the current price the new reference and re-arm it.

    Useful after a pricing correction (e.g. luggage) so a non-real change doesn't
    keep showing as a drop/increase.
    """
    item = db.get(TrackedItem, track_id)
    if item is None:
        return None
    if item.current_price is not None:
        item.initial_price = item.current_price
    item.status = TrackStatus.ACTIVE
    db.commit()
    db.refresh(item)
    return item


def get_active_tracks(db: Session) -> list[TrackedItem]:
    return list(db.scalars(select(TrackedItem).where(TrackedItem.status == TrackStatus.ACTIVE)))


def record_price(
    db: Session,
    item: TrackedItem,
    price: Decimal,
    hotel_portion: Decimal | None = None,
    flight_portion: Decimal | None = None,
) -> None:
    db.add(
        PriceHistory(
            tracked_item_id=item.id,
            price=price,
            hotel_portion=hotel_portion,
            flight_portion=flight_portion,
        )
    )
    item.current_price = price
    db.commit()
