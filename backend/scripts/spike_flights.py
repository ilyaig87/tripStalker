"""Spike: validate the Travelpayouts flight "price radar" with a real token.

Usage (from backend/, venv active):
    python scripts/spike_flights.py TLV BCN
    python scripts/spike_flights.py TLV LCA 2026-09-10 2026-09-13

Prints the cheapest fares recently observed on the route, by source (gate),
exactly as the app would store them in `compare_offers`.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date

sys.path.insert(0, ".")

from app.comparison import fetch_offers  # noqa: E402
from app.config import settings  # noqa: E402


async def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    origin, dest = sys.argv[1].upper(), sys.argv[2].upper()
    depart = date.fromisoformat(sys.argv[3]) if len(sys.argv) > 3 else None
    ret = date.fromisoformat(sys.argv[4]) if len(sys.argv) > 4 else None

    if not settings.travelpayouts_token:
        print("❌ TRAVELPAYOUTS_TOKEN is empty in .env.")
        sys.exit(2)

    res = await fetch_offers(origin, dest, depart, ret, currency="USD")
    offers = res["offers"]
    print(f"{origin}→{dest}: {len(offers)} offer(s)\n")
    for o in offers:
        print(f"  {o['agency']:<16} {o['currency']} {o['price']:>8}  {o.get('note',''):<12} {o['url']}")
    print("\nraw JSON stored in compare_offers:\n" + json.dumps(offers, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
