"""Daily price-check worker (CLI / system-cron entry point).

Run manually or from system cron:
    0 8 * * *  cd /path/to/TripStalker/backend && /path/to/venv/bin/python worker.py

The actual checking logic lives in app/price_check.py so it is shared with the
serverless Cron endpoint (/api/cron/check-prices) used on Vercel.
"""
from __future__ import annotations

import asyncio
import logging

from app.database import SessionLocal, init_db
from app.price_check import run_price_checks

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("tripstalker.worker")


async def run() -> None:
    init_db()
    db = SessionLocal()
    try:
        summary = await run_price_checks(db)
        logger.info("Worker run complete: %s", summary)
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(run())
