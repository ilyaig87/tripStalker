"""Shared price-check logic, used by BOTH the CLI worker (worker.py) and the
serverless Cron endpoint (/api/cron/check-prices). Keeping it in one place means
the two entry points can never drift apart.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from app.adapters import ProviderError, get_adapter
from app.config import settings
from app.crud import get_active_tracks, get_tracks_by_email, record_price
from app.models import TrackedItem, TrackStatus
from app.notifications import notify_price_drop
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

    baseline: Decimal = item.current_price or item.initial_price or result.price
    record_price(db, item, result.price)
    logger.info("Track %s: %s -> %s %s", item.id, baseline, result.price, result.currency)

    threshold = baseline * Decimal(str(settings.price_drop_threshold))
    if result.price < baseline - threshold:
        notify_price_drop(
            email=item.user.email,
            hotel_name=item.hotel_name or item.target_hotel_id_or_name,
            old_price=baseline,
            new_price=result.price,
            currency=result.currency,
        )
        item.status = TrackStatus.TRIGGERED
        db.commit()
        return {
            "track_id": item.id,
            "old_price": float(baseline),
            "new_price": float(result.price),
            "currency": result.currency,
        }
    return None


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
