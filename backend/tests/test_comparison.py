"""Flight "price radar" comparison is BEST-EFFORT: it must never break the core
price check, only runs for tracks with a derivable route (Travelist), and with
no token returns nothing.
"""
import asyncio
import json
from datetime import date
from decimal import Decimal

from app import price_check
from app.adapters.base import PriceResult
from app.comparison import derive_route, fetch_offers
from app.database import SessionLocal
from app.models import TrackedItem, TrackStatus, User


class _FakeAdapter:
    async def fetch_current_price(self, parsed):
        return PriceResult(price=Decimal("100.00"), currency="USD", hotel_name="✈️ TLV-BCN")


def _make_track(db, email: str, *, provider="travelist", route="TLV-BCN") -> TrackedItem:
    user = User(email=email)
    db.add(user)
    db.flush()
    item = TrackedItem(
        user_id=user.id,
        provider=provider,
        raw_url="https://example/x",
        currency="USD",
        status=TrackStatus.ACTIVE,
        initial_price=Decimal("120.00"),
        current_price=Decimal("120.00"),
        target_hotel_id_or_name=route,
        check_in_date=date(2026, 9, 10),
        check_out_date=date(2026, 9, 13),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def test_fetch_offers_without_token_returns_empty():
    res = asyncio.run(fetch_offers("TLV", "BCN", date(2026, 9, 10), date(2026, 9, 13)))
    assert res["offers"] == []


def test_derive_route_only_for_flight_tracks():
    db = SessionLocal()
    try:
        flight = _make_track(db, "route-flight@test.com", provider="travelist", route="TLV-BCN")
        hotel = _make_track(db, "route-hotel@test.com", provider="holidayfinder", route="12345")
        assert derive_route(flight) == ("TLV", "BCN", date(2026, 9, 10), date(2026, 9, 13))
        assert derive_route(hotel) is None
    finally:
        db.close()


def test_comparison_failure_never_breaks_the_check(monkeypatch):
    db = SessionLocal()
    try:
        item = _make_track(db, "cmp-fail@test.com")
        monkeypatch.setattr(price_check, "get_adapter", lambda provider: _FakeAdapter())

        async def boom(*args, **kwargs):
            raise RuntimeError("flights api down")

        monkeypatch.setattr(price_check, "fetch_offers", boom)
        asyncio.run(price_check.check_one(db, item))
        db.refresh(item)
        assert item.current_price == Decimal("100.00")  # core check still recorded the price
        assert item.compare_offers is None
    finally:
        db.close()


def test_comparison_offers_are_stored_when_available(monkeypatch):
    db = SessionLocal()
    try:
        item = _make_track(db, "cmp-ok@test.com")
        monkeypatch.setattr(price_check, "get_adapter", lambda provider: _FakeAdapter())

        async def fake_offers(*args, **kwargs):
            return {"offers": [{"agency": "Kupi.com", "price": 95.0, "url": "http://a", "currency": "USD", "note": "10.09"}]}

        monkeypatch.setattr(price_check, "fetch_offers", fake_offers)
        asyncio.run(price_check.check_one(db, item))
        db.refresh(item)
        stored = json.loads(item.compare_offers)
        assert stored[0]["agency"] == "Kupi.com"
    finally:
        db.close()
