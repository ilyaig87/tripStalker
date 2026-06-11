"""Pydantic request/response schemas (API contract)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl

from app.models import TrackStatus


# ---- auth ----
class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: EmailStr
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class TrackCreate(BaseModel):
    # The owner is taken from the auth token, not the request body.
    url: HttpUrl


class PriceHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    price: Decimal
    hotel_portion: Decimal | None = None
    flight_portion: Decimal | None = None
    checked_at: datetime


class TrackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider: str
    raw_url: str
    destination: str | None
    check_in_date: date | None
    check_out_date: date | None
    room_config: str | None
    target_hotel_id_or_name: str | None
    hotel_name: str | None
    hotel_url: str | None = None
    destination_city: str | None = None
    destination_photo_url: str | None = None
    initial_price: Decimal | None
    current_price: Decimal | None
    price_low: Decimal | None = None   # lowest price ever recorded
    price_high: Decimal | None = None  # highest price ever recorded
    currency: str
    status: TrackStatus
    available: bool = True
    last_error: str | None = None
    last_checked_at: datetime | None = None
    alt_price: Decimal | None = None
    alt_check_in: date | None = None
    alt_check_out: date | None = None
    alt_url: str | None = None
    alt_details: str | None = None
    hotel_portion: Decimal | None = None
    flight_portion: Decimal | None = None
    flight_details: str | None = None
    hotel_meta: str | None = None  # JSON: stars, reviews, board, room, tags, photo, maps…
    created_at: datetime


class TrackDetailOut(TrackOut):
    price_history: list[PriceHistoryOut] = []
