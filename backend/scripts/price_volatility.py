#!/usr/bin/env python3
"""price_volatility.py — measure how often a HolidayFinder offer's price moves.

100% free: hits the same no-auth HolidayFinder API the adapter uses, appends one
row per run to a CSV, and (in --analyze mode) reports how often / how much the
price actually changed. Use it to CALIBRATE the worker's check cadence with real
data instead of a rule of thumb.

Two modes:

  # 1) Sample once — append the current price to the CSV (run this from cron).
  .venv/bin/python scripts/price_volatility.py "<hf url>"

  # 2) Analyze what's been collected so far.
  .venv/bin/python scripts/price_volatility.py --analyze

Schedule it for free with your Mac's crontab (every 3 hours):
  crontab -e   then add:
  0 */3 * * * cd /Users/ilya/PhpstormProjects/TripStalker/backend && \
    .venv/bin/python scripts/price_volatility.py "<hf url>" >> /tmp/hf_vol.log 2>&1

Leave it for 2-3 days, then run --analyze.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.adapters.holidayfinder_adapter import HolidayFinderAdapter  # noqa: E402
from app.url_parser import parse_url  # noqa: E402

CSV_PATH = Path(__file__).resolve().parent / "price_volatility.csv"
_FIELDS = ["ts_utc", "total", "hotel_portion", "flight_portion", "currency", "url"]


async def sample(url: str) -> None:
    parsed = parse_url(url)
    if parsed.provider != "holidayfinder":
        raise SystemExit(f"Not a HolidayFinder URL (detected: {parsed.provider}).")
    pr = await HolidayFinderAdapter().fetch_current_price(parsed)
    row = {
        "ts_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total": f"{pr.price}",
        "hotel_portion": f"{pr.hotel_portion}" if pr.hotel_portion is not None else "",
        "flight_portion": f"{pr.flight_portion}" if pr.flight_portion is not None else "",
        "currency": pr.currency,
        "url": url,
    }
    new_file = not CSV_PATH.exists()
    with CSV_PATH.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_FIELDS)
        if new_file:
            w.writeheader()
        w.writerow(row)
    print(f"{row['ts_utc']}  total={pr.currency} {pr.price}  "
          f"(hotel {pr.hotel_portion} / flight {pr.flight_portion})  -> {CSV_PATH.name}")


def analyze() -> None:
    if not CSV_PATH.exists():
        raise SystemExit(f"No data yet at {CSV_PATH}. Run a few samples first.")
    rows = list(csv.DictReader(CSV_PATH.open()))
    if len(rows) < 2:
        print(f"Only {len(rows)} sample(s) — need at least 2 to measure movement.")
        return

    def ts(r):
        return datetime.fromisoformat(r["ts_utc"])

    def total(r):
        return float(r["total"]) if r["total"] else None

    rows.sort(key=ts)
    span_h = (ts(rows[-1]) - ts(rows[0])).total_seconds() / 3600
    totals = [total(r) for r in rows if total(r) is not None]

    # Detect changes between consecutive samples.
    changes = []  # (when, prev, now, delta)
    for a, b in zip(rows, rows[1:]):
        pa, pb = total(a), total(b)
        if pa is not None and pb is not None and pa != pb:
            changes.append((ts(b), pa, pb, pb - pa))

    print(f"\n  Samples      : {len(rows)} over {span_h:.1f}h "
          f"({ts(rows[0]):%Y-%m-%d %H:%M} → {ts(rows[-1]):%Y-%m-%d %H:%M} UTC)")
    print(f"  Price range  : {min(totals):.0f} – {max(totals):.0f} "
          f"(spread {max(totals) - min(totals):.0f})")
    print(f"  Changes      : {len(changes)}")
    if changes:
        gaps = [(changes[i][0] - changes[i - 1][0]).total_seconds() / 3600
                for i in range(1, len(changes))]
        first_gap = (changes[0][0] - ts(rows[0])).total_seconds() / 3600
        print(f"  Avg gap between changes : "
              f"{(sum(gaps) / len(gaps)):.1f}h" if gaps else
              f"  First change after       : {first_gap:.1f}h")
        biggest = max(changes, key=lambda c: abs(c[3]))
        print(f"  Biggest single move     : {biggest[3]:+.0f} "
              f"({biggest[1]:.0f} → {biggest[2]:.0f}) at {biggest[0]:%m-%d %H:%M}")
        print("\n  All moves:")
        for when, pa, pb, d in changes:
            print(f"    {when:%m-%d %H:%M}  {pa:.0f} → {pb:.0f}  ({d:+.0f})")
        # Cadence hint.
        moves_per_day = len(changes) / (span_h / 24) if span_h else 0
        print(f"\n  → ~{moves_per_day:.1f} price move(s)/day. "
              f"A check every {max(6, round(24 / max(moves_per_day, 0.5) / 2)):d}h would "
              f"catch them without over-polling.")
    else:
        print(f"  → Price was STABLE across {span_h:.1f}h. "
              f"Daily checks are plenty for this offer.")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?", help="HolidayFinder offer URL (sample mode)")
    ap.add_argument("--analyze", action="store_true", help="report movement from the CSV")
    args = ap.parse_args()
    if args.analyze:
        analyze()
    elif args.url:
        await sample(args.url)
    else:
        ap.error("provide a HolidayFinder URL to sample, or --analyze")


if __name__ == "__main__":
    asyncio.run(main())
