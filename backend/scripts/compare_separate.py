#!/usr/bin/env python3
"""compare_separate.py — package vs. à-la-carte price check.

Takes a HolidayFinder offer URL and answers the question:
    "Is the flight+hotel PACKAGE actually cheaper than booking the
     same hotel and the same flight SEPARATELY?"

It pulls three numbers and lays them side by side (all USD):

  1. HolidayFinder package  -> the REAL package total (and the adapter's
     derived hotel/flight split, which is only an estimate — see below).
  2. Hotel booked alone      -> Xotelo (free, no key) — TripAdvisor-sourced
     OTA rates (Booking.com / Hotels.com / Expedia ...).
  3. Flight booked alone     -> Travelpayouts Aviasales Data API (free token).

Why the package "hotel split" is only an estimate: HolidayFinder gives ONE
package price. The adapter derives flight = the "naked" flight rate and
hotel = total - naked. Package suppliers cross-subsidise (wholesale hotel
rates, shifted markup), so that split is internally consistent for tracking
trends but is NOT the hotel's real market price. This script gets the real
market prices from independent sources so you can see the true gap.

Usage:
    .venv/bin/python scripts/compare_separate.py "<holidayfinder offer url>"
    .venv/bin/python scripts/compare_separate.py "<url>" --dump   # full HF JSON

Requires (for the flight leg only): TRAVELPAYOUTS_TOKEN in backend/.env
(free — register at travelpayouts.com). The hotel leg needs no key.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# Make `app` importable when run from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.adapters._http import get_json  # noqa: E402
from app.adapters.holidayfinder_adapter import HolidayFinderAdapter, _HEADERS  # noqa: E402
from app.config import settings  # noqa: E402
from app.url_parser import parse_url  # noqa: E402

_API_BASE = "https://www.holidayfinder.co.il/api_no_auth/package_search/hf-offer"
_XOTELO = "https://data.xotelo.com/api"
_TP = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"


# --------------------------------------------------------------------------- HF
async def fetch_hf(parsed) -> dict:
    """Raw HolidayFinder package response (same call the adapter makes)."""
    qs = parse_qs(urlparse(parsed.raw_url).query)
    q = lambda k, d="": qs[k][0] if qs.get(k) else d
    bc = q("bc")
    if not bc:
        raise SystemExit("URL is missing the `bc` booking code.")
    params = {
        "adult": q("adult", "2"),
        "child": q("child", "[]"),
        "airports[]": q("airports[]", "TLV"),
        "lang": "he",
        "muid": uuid.uuid4().hex,
        "tt": str(int(time.time() * 1000)),
    }
    return await get_json(f"{_API_BASE}/{bc}", params=params, headers=_HEADERS, proxy=settings.proxy_url or None)


def find_dest_iata(data: dict, origin: str | None) -> str | None:
    """Scan the HF JSON for the ARRIVAL airport IATA (the destination).

    HF doesn't put the destination in the URL, so we dig it out of the flight
    segments in the response. We collect every 3-letter airport code that isn't
    the origin and isn't an obvious connection home, preferring the last
    outbound segment's arrival.
    """
    codes: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                kl = k.lower()
                if isinstance(v, str) and re.fullmatch(r"[A-Z]{3}", v) and (
                    "airport" in kl or "arrival" in kl or kl in {"to", "dest", "destination", "iata"}
                ):
                    codes.append(v)
                walk(v)
        elif isinstance(node, list):
            for it in node:
                walk(it)

    walk(data)
    for c in codes:
        if c != (origin or "TLV"):
            return c
    return codes[0] if codes else None


def find_hotel(data: dict) -> tuple[str | None, str | None]:
    """Return (hotel_name, city/location hint) from the recommended rate."""
    rate = (data.get("data") or {}).get("recommendedRate") or {}
    hotel = rate.get("hotel") or {}
    name = hotel.get("name")
    loc = hotel.get("city") or hotel.get("destination") or hotel.get("country") or hotel.get("location")
    return name, (loc if isinstance(loc, str) else None)


# ------------------------------------------------------------------------ Xotelo
async def hotel_alone(name: str, loc: str | None, chk_in: str, chk_out: str,
                      adults: str, child_ages: list[str]) -> dict | None:
    """Cheapest independent rate for this hotel via Xotelo (no key needed)."""
    query = f"{name} {loc}".strip() if loc else name
    try:
        found = await get_json(f"{_XOTELO}/search", params={"query": query})
    except (httpx.HTTPError, ValueError) as e:
        return {"error": f"search failed: {e}"}
    lst = ((found.get("result") or {}).get("list")) or []
    if not lst:
        return {"error": f"no Xotelo match for {query!r}"}
    hit = lst[0]
    params = {
        "hotel_key": hit["hotel_key"], "chk_in": chk_in, "chk_out": chk_out,
        "currency": "USD", "rooms": "1", "adults": adults,
    }
    if child_ages:
        params["age_of_children"] = ",".join(child_ages)
    try:
        rates = await get_json(f"{_XOTELO}/rates", params=params)
    except (httpx.HTTPError, ValueError) as e:
        return {"error": f"rates failed: {e}", "matched": hit.get("name")}
    rlist = ((rates.get("result") or {}).get("rates")) or []
    priced = [r for r in rlist if isinstance(r.get("rate"), (int, float)) and r["rate"] > 0]
    if not priced:
        return {"error": "no rates returned (dates unavailable?)", "matched": hit.get("name")}
    best = min(priced, key=lambda r: r["rate"])
    return {"matched": hit.get("name"), "vendor": best.get("name"),
            "price": float(best["rate"]), "all": sorted((r["name"], r["rate"]) for r in priced)}


# ------------------------------------------------------------------- Travelpayouts
async def flight_alone(origin: str, dest: str, depart: str, ret: str) -> dict | None:
    """Cheapest round-trip TLV->dest via Travelpayouts (needs free token)."""
    token = settings.travelpayouts_token
    if not token:
        return {"error": "TRAVELPAYOUTS_TOKEN not set in backend/.env (free at travelpayouts.com)"}
    params = {
        "origin": origin, "destination": dest,
        "departure_at": depart, "return_at": ret,
        "currency": "usd", "one_way": "false", "limit": "10",
        "sorting": "price", "token": token,
    }
    try:
        data = await get_json(_TP, params=params)
    except (httpx.HTTPError, ValueError) as e:
        return {"error": f"flight lookup failed: {e}"}
    rows = [r for r in (data.get("data") or []) if isinstance(r.get("price"), (int, float))]
    if not rows:
        return {"error": f"no flights found {origin}->{dest} for those dates"}
    best = min(rows, key=lambda r: r["price"])
    return {"price": float(best["price"]), "airline": best.get("airline"),
            "depart": best.get("departure_at"), "ret": best.get("return_at")}


# ------------------------------------------------------------------------- report
def usd(x) -> str:
    return f"${x:,.0f}" if isinstance(x, (int, float)) else "—"


def line(label: str, pkg, sep, note=""):
    diff = (sep - pkg) if isinstance(pkg, (int, float)) and isinstance(sep, (int, float)) else None
    dtxt = ""
    if diff is not None:
        sign = "+" if diff > 0 else ""
        verdict = "package cheaper ✅" if diff > 0 else ("separate cheaper ⚠️" if diff < 0 else "equal")
        dtxt = f"{sign}{diff:,.0f}  {verdict}"
    print(f"  {label:<26}{usd(pkg):>12}{usd(sep):>16}   {dtxt or note}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--dump", action="store_true", help="print the full HolidayFinder JSON and exit")
    args = ap.parse_args()

    parsed = parse_url(args.url)
    if parsed.provider != "holidayfinder":
        raise SystemExit(f"Not a HolidayFinder URL (detected: {parsed.provider}).")

    print("Fetching HolidayFinder package …")
    data = await fetch_hf(parsed)
    if args.dump:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    # 1) Package numbers via the existing adapter (real total + derived split).
    pr = HolidayFinderAdapter()._extract_price(
        data, parsed, luggage_tier=(parse_qs(urlparse(parsed.raw_url).query).get("bc", [""])[0].split(":") + ["", ""])[1] or None
    )
    pkg_total = float(pr.price)
    pkg_hotel = float(pr.hotel_portion) if pr.hotel_portion is not None else None
    pkg_flight = float(pr.flight_portion) if pr.flight_portion is not None else None

    # 2) Resolve the bits we need for the separate lookups.
    origin = parsed.extra.get("departure_airport") or "TLV"
    dest = find_dest_iata(data, origin)
    hotel_name, hotel_loc = find_hotel(data)
    chk_in = parsed.check_in_date.isoformat() if parsed.check_in_date else None
    chk_out = parsed.check_out_date.isoformat() if parsed.check_out_date else None
    qs = parse_qs(urlparse(parsed.raw_url).query)
    adults = (qs.get("adult") or ["2"])[0]
    child_ages = re.findall(r"\d+", (qs.get("child") or [""])[0])

    print(f"\n  Hotel : {hotel_name or '?'}   ({hotel_loc or 'location unknown'})")
    print(f"  Route : {origin} -> {dest or '?'}   {chk_in} … {chk_out}   {adults} adult(s), {len(child_ages)} child")

    # 3) Independent prices (run concurrently).
    hotel_task = hotel_alone(hotel_name, hotel_loc, chk_in, chk_out, adults, child_ages) if hotel_name and chk_in else None
    flight_task = flight_alone(origin, dest, chk_in, chk_out) if dest and chk_in else None
    hotel_res, flight_res = await asyncio.gather(
        hotel_task or _none(), flight_task or _none(),
    )

    # 4) Side-by-side.
    print("\n" + "=" * 72)
    print(f"  {'':<26}{'PACKAGE':>12}{'SEPARATE':>16}   DIFF (separate − package)")
    print("  " + "-" * 70)

    sep_hotel = hotel_res.get("price") if isinstance(hotel_res, dict) else None
    line("Hotel", pkg_hotel, sep_hotel,
         note=(hotel_res or {}).get("error", "") if not sep_hotel else "")
    sep_flight = flight_res.get("price") if isinstance(flight_res, dict) else None
    line("Flight (round-trip)", pkg_flight, sep_flight,
         note=(flight_res or {}).get("error", "") if not sep_flight else "")

    sep_total = (sep_hotel + sep_flight) if (sep_hotel and sep_flight) else None
    print("  " + "-" * 70)
    line("TOTAL", pkg_total, sep_total,
         note="(need both legs priced for a total)" if not sep_total else "")
    print("=" * 72)

    if isinstance(hotel_res, dict) and hotel_res.get("matched"):
        print(f"\n  Hotel match: {hotel_res['matched']}  | cheapest vendor: {hotel_res.get('vendor')}")
        if hotel_res.get("all"):
            print("  OTA rates: " + ", ".join(f"{n} {usd(r)}" for n, r in hotel_res["all"]))
    if isinstance(flight_res, dict) and flight_res.get("airline"):
        print(f"  Flight: {flight_res['airline']}  {flight_res.get('depart')} → {flight_res.get('ret')}")
    print("\n  NOTE: the PACKAGE hotel/flight columns are the adapter's derived split,")
    print("  not prices HolidayFinder quotes per-leg. Only the package TOTAL is exact.")


async def _none():
    return None


if __name__ == "__main__":
    asyncio.run(main())
