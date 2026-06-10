"""Pydantic request/response schemas (API contract)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, HttpUrl

from app.models import TrackStatus


class TrackCreate(BaseModel):
    email: EmailStr
    url: HttpUrl


class PriceHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    price: Decimal
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
    initial_price: Decimal | None
    current_price: Decimal | None
    currency: str
    status: TrackStatus
    available: bool = True
    last_error: str | None = None
    last_checked_at: datetime | None = None
    created_at: datetime


class TrackDetailOut(TrackOut):
    price_history: list[PriceHistoryOut] = []
