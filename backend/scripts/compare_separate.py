#!/usr/bin/env python3
"""compare_separate.py — package vs. à-la-carte price check (100% free sources).

Takes a HolidayFinder offer URL and answers:
    "Is the flight+hotel PACKAGE actually cheaper than booking the same
     hotel and the same flight SEPARATELY?"

It is deliberately HONEST about when the question can't be answered, because
two things routinely make a package non-comparable per-leg:

  * CHARTER flights  — HolidayFinder often flies charter (e.g. Arkia/Israir
    seat blocks). Charters are NOT sold separately and appear on NO flight
    API/site, so there is no "book the flight alone" price to compare to.
  * BOARD basis      — the package hotel is frequently All-Inclusive (all
    meals + drinks for everyone). Independent hotel APIs quote ROOM-ONLY, so
    the package "hotel portion" includes food the room rate doesn't. Comparing
    them straight is apples-to-oranges; we flag it instead of pretending.

Numbers, all USD, all free:
  1. HolidayFinder package -> real package total (+ the adapter's derived split,
     which is only an estimate — see notes printed at the end).
  2. Hotel alone           -> Xotelo /rates (free, no key). If the exact dates
     have no cached rates (common 3+ months out), we fall back to the nearest
     dates that do, same number of nights, and label it a PROXY.
  3. Flight alone          -> only attempted for SCHEDULED flights, via
     Travelpayouts (free token, NO credit card — register at travelpayouts.com).
     Skipped with an explanation for charter flights.

Finding the hotel key: Xotelo's /search is now RapidAPI-gated, but /rates is
still free. So pass the hotel's TripAdvisor id with --hotel-key. Find it in 5s:
google "<hotel name> tripadvisor", open the result, copy the gNNNN-dNNNN from
the URL (…/Hotel_Review-g190384-d236327-Reviews-…). Example below.

Usage:
    .venv/bin/python scripts/compare_separate.py "<hf url>" --hotel-key g190384-d236327
    .venv/bin/python scripts/compare_separate.py "<hf url>" --dump   # full HF JSON
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.adapters._http import get_json  # noqa: E402
from app.adapters.holidayfinder_adapter import HolidayFinderAdapter, _HEADERS  # noqa: E402
from app.config import settings  # noqa: E402
from app.url_parser import parse_url  # noqa: E402

_API_BASE = "https://www.holidayfinder.co.il/api_no_auth/package_search/hf-offer"
_XOTELO = "https://data.xotelo.com/api"
_TP = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"

# Board codes HolidayFinder/Travelyo use in the `bc` (…:tier:BOARD).
_BOARD = {
    "RO": "Room only", "OB": "Room only", "SC": "Self catering",
    "BB": "Bed & breakfast", "HB": "Half board", "FB": "Full board",
    "AI": "All-inclusive", "UAI": "Ultra all-inclusive", "AI+": "All-inclusive+",
}
_MEAL_INCLUSIVE = {"BB", "HB", "FB", "AI", "UAI", "AI+"}  # board that bundles food


# --------------------------------------------------------------------------- HF
async def fetch_hf(parsed) -> dict:
    qs = parse_qs(urlparse(parsed.raw_url).query)
    q = lambda k, d="": qs[k][0] if qs.get(k) else d
    bc = q("bc")
    if not bc:
        raise SystemExit("URL is missing the `bc` booking code.")
    params = {
        "adult": q("adult", "2"), "child": q("child", "[]"),
        "airports[]": q("airports[]", "TLV"), "lang": "he",
        "muid": uuid.uuid4().hex, "tt": str(int(time.time() * 1000)),
    }
    return await get_json(f"{_API_BASE}/{bc}", params=params, headers=_HEADERS, proxy=settings.proxy_url or None)


def flight_meta(data: dict) -> dict:
    """Pull flight type + route from the recommended rate's cheapest flight."""
    f = (data.get("data") or {}).get("recommendedRate", {}).get("cheapest_flight_data") or {}
    ftype = (f.get("flight_type") or "").lower()  # "charter" | "regular"/"scheduled"
    return {
        "charter": ftype == "charter",
        "type": ftype or "unknown",
        "company": f.get("company_name"),
        "dest_iata": f.get("city_to_code") or f.get("city_to_airport_code"),
        "dest_name": f.get("city_to_name"),
        "origin_iata": f.get("city_from_code") or f.get("city_from_airport_code"),
    }


def board_from_bc(raw_url: str) -> str | None:
    bc = (parse_qs(urlparse(raw_url).query).get("bc") or [""])[0]
    parts = bc.split(":")
    return parts[2].upper() if len(parts) >= 3 else None


def hotel_name(data: dict) -> str | None:
    return ((data.get("data") or {}).get("recommendedRate", {}).get("hotel") or {}).get("name")


# ------------------------------------------------------------------------ Xotelo
async def xotelo_rates(key: str, ci: str, co: str, adults: str, child_ages: list[str]) -> list[dict]:
    """Xotelo lead-in rates. Each rate is {code,name,rate,tax} — NO board field
    is exposed, so we treat `rate+tax` as the all-in price and the board basis as
    UNDISCLOSED (the lead-in rate is almost always room-only)."""
    params = {"hotel_key": key, "chk_in": ci, "chk_out": co, "currency": "USD", "rooms": "1", "adults": adults}
    if child_ages:
        params["age_of_children"] = ",".join(child_ages)
    try:
        d = await get_json(f"{_XOTELO}/rates", params=params)
    except (httpx.HTTPError, ValueError):
        return []
    out = []
    for r in ((d.get("result") or {}).get("rates")) or []:
        if isinstance(r.get("rate"), (int, float)) and r["rate"] > 0:
            out.append({"name": r["name"], "total": float(r["rate"]) + float(r.get("tax") or 0)})
    return out


async def hotel_alone(key: str, ci, co, adults: str, child_ages: list[str]) -> dict:
    """Cheapest room-only rate via Xotelo, with nearest-date fallback.

    Far-future dates often have no cached rates. We then probe the same number
    of nights shifted by ±1..4 weeks and return the nearest one that prices,
    clearly marked as a proxy.
    """
    nights = (co - ci).days
    exact = await xotelo_rates(key, ci.isoformat(), co.isoformat(), adults, child_ages)
    if exact:
        best = min(exact, key=lambda r: r["total"])
        return {"price": best["total"], "vendor": best["name"], "proxy": None,
                "all": sorted((r["name"], r["total"]) for r in exact)}
    for weeks in (1, 2, 3, 4, -1, -2):  # prefer earlier dates (likelier cached)
        alt_ci = ci - timedelta(weeks=weeks)
        alt_co = alt_ci + timedelta(days=nights)
        got = await xotelo_rates(key, alt_ci.isoformat(), alt_co.isoformat(), adults, child_ages)
        if got:
            best = min(got, key=lambda r: r["total"])
            return {"price": best["total"], "vendor": best["name"],
                    "proxy": f"{alt_ci.isoformat()}…{alt_co.isoformat()}",
                    "all": sorted((r["name"], r["total"]) for r in got)}
    return {"error": "no Xotelo rates for these or nearby dates (TripAdvisor cache empty this far out)"}


# ------------------------------------------------------------------- Travelpayouts
async def flight_alone(origin: str, dest: str, depart: str, ret: str) -> dict:
    token = settings.travelpayouts_token
    if not token:
        return {"error": "needs a FREE Travelpayouts token (no credit card) — see travelpayouts.com, "
                         "then add TRAVELPAYOUTS_TOKEN=… to backend/.env"}
    params = {"origin": origin, "destination": dest, "departure_at": depart, "return_at": ret,
              "currency": "usd", "one_way": "false", "limit": "10", "sorting": "price", "token": token}
    try:
        d = await get_json(_TP, params=params)
    except (httpx.HTTPError, ValueError) as e:
        return {"error": f"lookup failed: {e}"}
    rows = [r for r in (d.get("data") or []) if isinstance(r.get("price"), (int, float))]
    if not rows:
        return {"error": f"no SCHEDULED flights {origin}->{dest} for those dates"}
    best = min(rows, key=lambda r: r["price"])
    return {"price": float(best["price"]), "airline": best.get("airline")}


# ------------------------------------------------------------------------- report
def usd(x):
    return f"${x:,.0f}" if isinstance(x, (int, float)) else "—"


def row(label, pkg, sep, note="", comparable=True):
    """Render a comparison row. When the two sides aren't on the same board basis
    (`comparable=False`) we deliberately DON'T print a cheaper/pricier verdict —
    only the raw numbers — because the comparison would be misleading."""
    diff = (sep - pkg) if isinstance(pkg, (int, float)) and isinstance(sep, (int, float)) else None
    dtxt = note
    if diff is not None:
        if not comparable:
            dtxt = note or "≠ different board — not directly comparable"
        else:
            sign = "+" if diff > 0 else ""
            verdict = "package cheaper ✅" if diff > 0 else ("separate cheaper ⚠️" if diff < 0 else "equal")
            dtxt = f"{sign}{diff:,.0f}  {verdict}"
    print(f"  {label:<22}{usd(pkg):>11}{usd(sep):>14}   {dtxt}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--hotel-key", help="TripAdvisor id, e.g. g190384-d236327 (from the hotel's TripAdvisor URL)")
    ap.add_argument("--dump", action="store_true")
    args = ap.parse_args()

    parsed = parse_url(args.url)
    if parsed.provider != "holidayfinder":
        raise SystemExit(f"Not a HolidayFinder URL (detected: {parsed.provider}).")

    print("Fetching HolidayFinder package …")
    data = await fetch_hf(parsed)
    if args.dump:
        print(json.dumps(data, ensure_ascii=False, indent=2)); return

    # 1) Package numbers (real total + derived split) via the existing adapter.
    #    Pass the luggage tier from the bc so the total matches what HF charges.
    board_code = board_from_bc(parsed.raw_url)
    bc_parts = (parse_qs(urlparse(parsed.raw_url).query).get("bc") or [""])[0].split(":")
    luggage_tier = bc_parts[1] if len(bc_parts) >= 2 else None
    pr = HolidayFinderAdapter()._extract_price(data, parsed, luggage_tier=luggage_tier)
    pkg_total = float(pr.price)
    pkg_hotel = float(pr.hotel_portion) if pr.hotel_portion is not None else None
    pkg_flight = float(pr.flight_portion) if pr.flight_portion is not None else None

    fm = flight_meta(data)
    name = hotel_name(data)
    ci, co = parsed.check_in_date, parsed.check_out_date
    qs = parse_qs(urlparse(parsed.raw_url).query)
    adults = (qs.get("adult") or ["2"])[0]
    child_ages = re.findall(r"\d+", (qs.get("child") or [""])[0])
    board_label = _BOARD.get(board_code or "", board_code or "?")

    print(f"\n  Hotel : {name or '?'}    board: {board_label}")
    print(f"  Route : {fm['origin_iata']} → {fm['dest_iata']} ({fm['dest_name']})    "
          f"{ci} … {co}    {adults} adult(s), {len(child_ages)} child")
    print(f"  Flight: {fm['company']}  [{fm['type'].upper()}]")

    # 2) Hotel alone (free Xotelo). Needs a hotel key.
    if args.hotel_key and name and ci and co:
        hotel_res = await hotel_alone(args.hotel_key, ci, co, adults, child_ages)
    elif not args.hotel_key:
        hotel_res = {"error": f"pass --hotel-key (google \"{name} tripadvisor\" → copy gNNNN-dNNNN)"}
    else:
        hotel_res = {"error": "missing hotel name or dates"}

    # 3) Flight alone — only meaningful for scheduled flights.
    if fm["charter"]:
        flight_res = {"error": "CHARTER flight — not sold separately, exists on no flight API"}
    elif fm["dest_iata"] and ci and co:
        flight_res = await flight_alone(fm["origin_iata"] or "TLV", fm["dest_iata"], ci.isoformat(), co.isoformat())
    else:
        flight_res = {"error": "missing route/dates"}

    # 4) Side-by-side.
    print("\n" + "=" * 70)
    print(f"  {'':<22}{'PACKAGE':>11}{'SEPARATE':>14}   DIFF (separate − package)")
    print("  " + "-" * 68)
    sep_hotel = hotel_res.get("price")
    row("Hotel", pkg_hotel, sep_hotel, note=hotel_res.get("error", ""))
    sep_flight = flight_res.get("price")
    row("Flight (round-trip)", pkg_flight, sep_flight, note=flight_res.get("error", ""))
    print("  " + "-" * 68)
    sep_total = (sep_hotel + sep_flight) if (sep_hotel and sep_flight) else None
    row("TOTAL", pkg_total, sep_total, note="(need both legs priced)" if not sep_total else "")
    print("=" * 70)

    # 5) Honesty footnotes — the two traps.
    notes = []
    if board_code in _MEAL_INCLUSIVE:
        notes.append(
            f"⚠ Board mismatch: the package hotel is {board_label} (food/drinks included for everyone), "
            f"but Xotelo quotes ROOM-ONLY. The package hotel column is NOT comparable to the separate "
            f"hotel price — the gap is mostly meals, not markup.")
    if fm["charter"]:
        notes.append(
            "⚠ The flight is CHARTER: it cannot be booked separately and appears on no flight API. "
            "There is no honest à-la-carte flight price to compare against.")
    if hotel_res.get("proxy"):
        notes.append(f"ℹ Hotel price is a PROXY from nearby dates ({hotel_res['proxy']}) — the exact "
                     "dates had no cached rates. Treat as a ballpark.")
    if hotel_res.get("all"):
        notes.append("Hotel OTA rates (room-only): " + ", ".join(f"{n} {usd(r)}" for n, r in hotel_res["all"]))
    notes.append("The PACKAGE hotel/flight columns are the adapter's derived split, not per-leg prices "
                 "HolidayFinder quotes. Only the package TOTAL is exact.")
    print()
    for n in notes:
        print("  " + n)


if __name__ == "__main__":
    asyncio.run(main())
