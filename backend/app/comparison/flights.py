"""Flight price radar via the Travelpayouts (Aviasales) flights data API.

Hotellook (hotels) was shut down in 2025, so cross-provider HOTEL comparison is
gone. Flights, however, are reachable for free with the partner token. The data
is CACHED/approximate ("cheapest fares users recently searched"), so this is a
"route price radar" — the cheapest fares seen lately on a route, by source/date
— not a live same-trip quote. The UI labels it as such.

Best-effort: any missing token / network error / odd payload → empty offers.
"""
from __future__ import annotations

import logging
import re
from datetime import date

import httpx

from app.adapters._http import get_json
from app.config import settings
from app.models import TrackedItem

logger = logging.getLogger("tripstalker.compare.flights")

_LATEST_URL = "https://api.travelpayouts.com/v2/prices/latest"
_MAX_OFFERS = 6
_ROUTE_RE = re.compile(r"^([A-Za-z]{3})-([A-Za-z]{3})$")


def derive_route(item: TrackedItem) -> tuple[str, str, date | None, date | None] | None:
    """(origin, dest, depart, return) for tracks whose price IS a flight total.

    Today that's Travelist flight searches, which store the route as
    `target_hotel_id_or_name = "TLV-BCN"`. Hotel/package providers return None
    (a flight-only price isn't comparable to a hotel/package total).
    """
    if item.provider != "travelist":
        return None
    m = _ROUTE_RE.match((item.target_hotel_id_or_name or "").strip())
    if not m or not item.check_in_date:
        return None
    return m.group(1).upper(), m.group(2).upper(), item.check_in_date, item.check_out_date


def _ddmm(d: str | None) -> str:
    """'2026-06-16' -> '1606' for an Aviasales deep-link."""
    if not d or len(d) < 10:
        return ""
    return d[8:10] + d[5:7]


def _deeplink(origin: str, dest: str, depart: str | None, ret: str | None, adults: int) -> str:
    url = f"https://www.aviasales.com/search/{origin}{_ddmm(depart)}{dest}{_ddmm(ret)}{adults}"
    if settings.travelpayouts_marker:
        url += f"?marker={settings.travelpayouts_marker}"
    return url


async def fetch_offers(
    origin: str | None,
    dest: str | None,
    depart: date | None,
    return_: date | None,
    currency: str = "USD",
    adults: int = 1,
) -> dict:
    """Return {"offers": [{agency, price, url, currency, note}]} — cheapest fares
    recently observed on the route, sorted cheapest-first. Empty on any problem."""
    token = settings.travelpayouts_token
    if not token or not (origin and dest):
        return {"offers": []}
    try:
        data = await get_json(
            _LATEST_URL,
            params={
                "origin": origin,
                "destination": dest,
                "currency": currency.lower(),
                "token": token,
                "limit": 30,
                "page": 1,
                "show_to_affiliates": "true",
                "sorting": "price",
                "period_type": "year",
            },
        )
    except (httpx.HTTPError, ValueError) as exc:
        logger.debug("Flights radar fetch failed %s-%s: %s", origin, dest, exc)
        return {"offers": []}

    records = (data or {}).get("data") or []
    offers: list[dict] = []
    for r in records:
        gate = r.get("gate")
        price = r.get("value")
        if not gate or not isinstance(price, (int, float)) or price <= 0:
            continue
        dep, ret = r.get("depart_date"), r.get("return_date")
        # "16.06" (+ return when it differs) as a small note on the row
        note = f"{dep[8:10]}.{dep[5:7]}" if dep and len(dep) >= 10 else ""
        if ret and ret != dep and len(ret) >= 10:
            note += f"–{ret[8:10]}.{ret[5:7]}"
        offers.append(
            {
                "agency": str(gate),
                "price": round(float(price), 2),
                "url": _deeplink(origin, dest, dep, ret, adults),
                "currency": currency.upper(),
                "note": note,
            }
        )
    # de-dupe identical (gate, price) rows; keep cheapest-first; cap.
    seen: set[tuple[str, float]] = set()
    unique = []
    for o in sorted(offers, key=lambda x: x["price"]):
        key = (o["agency"], o["price"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(o)
    return {"offers": unique[:_MAX_OFFERS]}
