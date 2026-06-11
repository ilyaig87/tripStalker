"""Shared price-check logic, used by BOTH the CLI worker (worker.py) and the
serverless Cron endpoint (/api/cron/check-prices). Keeping it in one place means
the two entry points can never drift apart.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
from sqlalchemy.orm import Session

from app.adapters import ProviderError, get_adapter
from app.adapters._http import get_json
from app.config import settings
from app.crud import get_active_tracks, get_tracks_by_email, record_price
from app.models import TrackedItem, TrackStatus
from app.notifications import notify_price_change
from app.url_parser import ParsedUrl

logger = logging.getLogger("tripstalker.price_check")

# Mark an offer "unavailable" only after this many consecutive failed fetches,
# so a single transient blip (rate limit, timeout) doesn't trip a false alarm.
UNAVAILABLE_AFTER = 2


def _parsed_from_item(item: TrackedItem) -> ParsedUrl:
    """Rebuild a ParsedUrl from stored columns (no need to re-parse raw_url)."""
    return ParsedUrl(
        provider=item.provider,
        raw_url=item.raw_url,
        destination=item.destination,
        check_in_date=item.check_in_date,
        check_out_date=item.check_out_date,
        room_config=item.room_config,
        target_hotel_id_or_name=item.target_hotel_id_or_name,
    )


async def check_one(db: Session, item: TrackedItem) -> dict | None:
    """Check a single track. Returns a summary dict if a drop was triggered."""
    adapter = get_adapter(item.provider)
    item.last_checked_at = datetime.now(timezone.utc)
    try:
        result = await adapter.fetch_current_price(_parsed_from_item(item))
    except ProviderError as exc:
        # Couldn't fetch a price — count the failure and, after a few in a row,
        # flag the offer as no longer available.
        item.failed_checks = (item.failed_checks or 0) + 1
        item.last_error = str(exc)[:500]
        if item.failed_checks >= UNAVAILABLE_AFTER and item.available:
            item.available = False
            logger.info("Track %s marked UNAVAILABLE after %d failures", item.id, item.failed_checks)
        db.commit()
        logger.warning("Track %s (%s) fetch failed: %s", item.id, item.provider, exc)
        return None

    # Success — clear any prior failure / unavailable state.
    if not item.available or item.failed_checks:
        item.available = True
        item.failed_checks = 0
        item.last_error = None

    # Refresh the package breakdown (hotel vs flight) if the adapter provides it.
    item.hotel_portion = result.hotel_portion
    item.flight_portion = result.flight_portion
    item.flight_details = result.flight_details
    # Self-heal the display name: the adapter resolves the real HOTEL name
    # (older rows sometimes stored the room type instead).
    if result.hotel_name:
        item.hotel_name = result.hotel_name
    if result.hotel_meta:
        item.hotel_meta = json.dumps(result.hotel_meta, ensure_ascii=False)
    if result.destination_city:
        item.destination_city = result.destination_city
    if result.hotel_url:
        item.hotel_url = result.hotel_url
    if item.destination_city and not item.destination_photo_url:
        item.destination_photo_url = await _fetch_destination_photo(item.destination_city)

    baseline: Decimal = item.current_price or item.initial_price or result.price
    record_price(db, item, result.price, result.hotel_portion, result.flight_portion)
    logger.info("Track %s: %s -> %s %s", item.id, baseline, result.price, result.currency)

    # Cheaper same-hotel/same-nights alternative on other dates (best-effort).
    alt = await _store_alternative(db, item, adapter, result.price)

    # Notify on a meaningful move in EITHER direction (drop = deal, rise = heads-up).
    threshold = baseline * Decimal(str(settings.price_drop_threshold))
    delta = result.price - baseline
    if abs(delta) > threshold:
        notify_price_change(
            email=item.user.email,
            hotel_name=item.hotel_name or item.target_hotel_id_or_name,
            old_price=baseline,
            new_price=result.price,
            currency=result.currency,
            link=item.raw_url,
            alternative=alt,
        )
        # Only a DROP marks the track as a "deal found"; a rise is informational.
        if delta < 0:
            item.status = TrackStatus.TRIGGERED
        db.commit()
        return {
            "track_id": item.id,
            "old_price": float(baseline),
            "new_price": float(result.price),
            "currency": result.currency,
            "direction": "rise" if delta > 0 else "drop",
        }
    return None


async def _store_alternative(db: Session, item: TrackedItem, adapter, current_price: Decimal) -> dict | None:
    """Find + store a cheaper alternative if the adapter supports it. Never raises."""
    finder = getattr(adapter, "find_cheaper_alternative", None)
    alt = None
    if finder:
        try:
            alt = await finder(_parsed_from_item(item), current_price)
        except Exception as exc:  # best-effort: a suggestion failure must not break the check
            logger.warning("Alternative finder failed for track %s: %s", item.id, exc)
    item.alt_price = Decimal(str(alt["price"])).quantize(Decimal("1.00")) if alt else None
    item.alt_check_in = date.fromisoformat(alt["check_in"]) if alt and alt.get("check_in") else None
    item.alt_check_out = date.fromisoformat(alt["check_out"]) if alt and alt.get("check_out") else None
    item.alt_url = alt.get("url") if alt else None
    item.alt_details = json.dumps(alt["details"]) if alt and alt.get("details") else None
    db.commit()
    return alt


async def _fetch_destination_photo(city: str) -> str | None:
    """A landscape destination photo from Unsplash (best-effort). Returns a CDN
    URL sized for a card banner, or None."""
    if not settings.unsplash_access_key:
        return None
    try:
        data = await get_json(
            "https://api.unsplash.com/search/photos",
            params={"query": city, "per_page": 1, "orientation": "landscape", "content_filter": "high"},
            headers={"Authorization": f"Client-ID {settings.unsplash_access_key}", "Accept-Version": "v1"},
        )
    except (httpx.HTTPError, ValueError):
        return None
    results = (data or {}).get("results") or []
    if not results:
        return None
    raw = (results[0].get("urls") or {}).get("raw")
    return f"{raw}&w=900&h=300&fit=crop&q=80" if raw else None


async def run_price_checks(db: Session) -> dict:
    """Check every active track. Returns a summary (for the Cron endpoint / logs)."""
    items = get_active_tracks(db)
    logger.info("Checking %d active track(s)...", len(items))
    triggered = []
    for item in items:
        result = await check_one(db, item)
        if result:
            triggered.append(result)
    return {"checked": len(items), "triggered": triggered}


async def run_price_checks_for_email(db: Session, email: str) -> dict:
    """On-demand re-check of one user's tracks (the dashboard 'Check now' button).

    Skips Expired tracks; re-checks Active/Triggered/Unavailable so prices refresh
    and a returned offer can recover its availability.
    """
    items = [t for t in get_tracks_by_email(db, email) if t.status != TrackStatus.EXPIRED]
    logger.info("On-demand check of %d track(s) for %s", len(items), email)
    triggered = []
    for item in items:
        result = await check_one(db, item)
        if result:
            triggered.append(result)
    return {"checked": len(items), "triggered": triggered}
