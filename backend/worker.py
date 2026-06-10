"""Daily price-check worker (the 'Scraper Engine').

Run from cron, e.g. add to crontab:
    0 8 * * *  cd /path/to/TripStalker/backend && /path/to/venv/bin/python worker.py

It loops over every Active tracked item, fetches the current price via the
matching adapter, records it in price_history, and fires a notification when
the price drops below the baseline by more than PRICE_DROP_THRESHOLD.
"""
from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

from app.adapters import ProviderError, get_adapter
from app.config import settings
from app.crud import get_active_tracks, record_price
from app.database import SessionLocal, init_db
from app.models import TrackStatus
from app.notifications import notify_price_drop
from app.url_parser import ParsedUrl

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("tripstalker.worker")


def _parsed_from_item(item) -> ParsedUrl:
    """Rebuild a ParsedUrl from stored columns (no need to re-parse the raw URL)."""
    return ParsedUrl(
        provider=item.provider,
        raw_url=item.raw_url,
        destination=item.destination,
        check_in_date=item.check_in_date,
        check_out_date=item.check_out_date,
        room_config=item.room_config,
        target_hotel_id_or_name=item.target_hotel_id_or_name,
    )


async def check_one(db, item) -> None:
    adapter = get_adapter(item.provider)
    try:
        result = await adapter.fetch_current_price(_parsed_from_item(item))
    except ProviderError as exc:
        logger.warning("Skipping track %s (%s): %s", item.id, item.provider, exc)
        return

    baseline: Decimal = item.current_price or item.initial_price or result.price
    record_price(db, item, result.price)
    logger.info("Track %s: %s -> %s %s", item.id, baseline, result.price, result.currency)

    threshold = baseline * Decimal(str(settings.price_drop_threshold))
    if result.price < baseline - threshold:
        notify_price_drop(
            email=item.user.email,
            hotel_name=item.target_hotel_id_or_name,
            old_price=baseline,
            new_price=result.price,
            currency=result.currency,
        )
        item.status = TrackStatus.TRIGGERED
        db.commit()


async def run() -> None:
    init_db()
    db = SessionLocal()
    try:
        items = get_active_tracks(db)
        logger.info("Checking %d active track(s)...", len(items))
        for item in items:
            await check_one(db, item)
    finally:
        db.close()
    logger.info("Worker run complete.")


if __name__ == "__main__":
    asyncio.run(run())
