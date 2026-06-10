"""URL Parser Engine.

Identifies the travel provider from a pasted URL and extracts a normalized
set of search parameters. The result is provider-agnostic so the rest of the
system can stay decoupled from any single site's URL shape.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import parse_qs, unquote, urlparse


@dataclass
class ParsedUrl:
    provider: str                       # 'booking', 'travelist', 'unknown'
    raw_url: str
    destination: str | None = None
    check_in_date: date | None = None
    check_out_date: date | None = None
    room_config: str | None = None      # normalized "N-adults,M-children"
    target_hotel_id_or_name: str | None = None
    # Everything else we parsed but didn't normalize, kept for the adapter to use.
    extra: dict[str, str] = field(default_factory=dict)


# Map a hostname fragment -> provider key. Extend this as you add adapters.
_DOMAIN_TO_PROVIDER: dict[str, str] = {
    "booking.com": "booking",
    "travelist.co.il": "travelist",
    "holidayfinder.co.il": "holidayfinder",
}


def _detect_provider(host: str) -> str:
    host = host.lower().removeprefix("www.")
    for domain, provider in _DOMAIN_TO_PROVIDER.items():
        if host == domain or host.endswith("." + domain) or host.endswith(domain):
            return provider
    return "unknown"


def _first(qs: dict[str, list[str]], *keys: str) -> str | None:
    for key in keys:
        if key in qs and qs[key]:
            return unquote(qs[key][0])
    return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def parse_url(url: str) -> ParsedUrl:
    """Main entry point used by the routing layer / endpoints."""
    parsed = urlparse(url.strip())
    provider = _detect_provider(parsed.netloc)
    qs = parse_qs(parsed.query)

    if provider == "booking":
        return _parse_booking(url, parsed.path, qs)
    if provider == "travelist":
        return _parse_travelist(url, parsed.path, qs)
    if provider == "holidayfinder":
        return _parse_holidayfinder(url, parsed.path, qs)

    return ParsedUrl(provider="unknown", raw_url=url, extra={k: v[0] for k, v in qs.items()})


def _parse_booking(url: str, path: str, qs: dict[str, list[str]]) -> ParsedUrl:
    """Booking.com hotel URLs, e.g.
    https://www.booking.com/hotel/gr/xyz.html?checkin=2026-07-01&checkout=2026-07-05
        &group_adults=2&group_children=1&dest_id=...&label=...
    """
    adults = _first(qs, "group_adults", "adults") or "2"
    children = _first(qs, "group_children", "children") or "0"
    room_config = _normalize_occupancy(adults, children)

    # The hotel slug lives in the path: /hotel/<country>/<slug>.html
    hotel_slug = None
    m = re.search(r"/hotel/[a-z]{2}/([^/.?]+)", path)
    if m:
        hotel_slug = m.group(1)

    return ParsedUrl(
        provider="booking",
        raw_url=url,
        destination=_first(qs, "ss", "dest_id", "city"),
        check_in_date=_parse_date(_first(qs, "checkin")),
        check_out_date=_parse_date(_first(qs, "checkout")),
        room_config=room_config,
        target_hotel_id_or_name=_first(qs, "hotel_id") or hotel_slug,
        extra={k: v[0] for k, v in qs.items()},
    )


def _parse_travelist(url: str, path: str, qs: dict[str, list[str]]) -> ParsedUrl:
    """Travelist.co.il URLs.

    Flight searches (/flightsResults) carry bracketed `segmentsClient[i][...]`
    params; we reconstruct origin/destination/dates from them. Older package/deal
    URLs fall back to the legacy query-param parsing below.
    """
    if "flightsResults" in path or any(k.startswith("segmentsClient") for k in qs):
        segs: dict[int, dict[str, str]] = {}
        for key, vals in qs.items():
            m = re.fullmatch(r"segmentsClient\[(\d+)\]\[(from|to|date)\]", key)
            if m and vals:
                segs.setdefault(int(m.group(1)), {})[m.group(2)] = vals[0]
        ordered = [segs[i] for i in sorted(segs)]
        origin = ordered[0].get("from") if ordered else None
        dest = ordered[0].get("to") if ordered else None
        return ParsedUrl(
            provider="travelist",
            raw_url=url,
            destination=dest,
            check_in_date=_parse_date(ordered[0].get("date")) if ordered else None,
            check_out_date=_parse_date(ordered[-1].get("date")) if len(ordered) > 1 else None,
            room_config=_normalize_occupancy(_first(qs, "adults") or "2", _first(qs, "children") or "0"),
            target_hotel_id_or_name=f"{origin}-{dest}" if origin and dest else None,
            extra={k: v[0] for k, v in qs.items()},
        )

    adults = _first(qs, "adults", "ad") or "2"
    children = _first(qs, "children", "ch", "kids") or "0"
    rooms = _first(qs, "rooms")
    room_config = _normalize_occupancy(adults, children, rooms)

    # Deal id often appears as /deal/<id> or /package/<id>
    deal_id = None
    m = re.search(r"/(?:deal|package|product)/(\d+)", path)
    if m:
        deal_id = m.group(1)

    return ParsedUrl(
        provider="travelist",
        raw_url=url,
        destination=_first(qs, "destination", "dest", "city"),
        check_in_date=_parse_date(_first(qs, "depart", "checkIn", "checkin", "from")),
        check_out_date=_parse_date(_first(qs, "return", "checkOut", "checkout", "to")),
        room_config=room_config,
        target_hotel_id_or_name=_first(qs, "hotelId", "dealId") or deal_id,
        extra={k: v[0] for k, v in qs.items()},
    )


def _parse_holidayfinder(url: str, path: str, qs: dict[str, list[str]]) -> ParsedUrl:
    """HolidayFinder.co.il package URLs (built on the Travelyo platform), e.g.
    https://www.holidayfinder.co.il/offer/6606726?bc=m4d32h6606726c21o150926i200926st1:recommended:AI
        &adult=2&child=[2]&airports[]=TLV&position=0

    The travel dates are NOT plain query params — they are encoded inside the
    `bc` "booking code": ...o<DDMMYY>i<DDMMYY>... (o = outbound, i = inbound).
    """
    # Offer id from the path: /offer/<id>
    offer_id = None
    m = re.search(r"/offer/(\d+)", path)
    if m:
        offer_id = m.group(1)

    # Decode dates + hotel id from the `bc` code.
    bc = _first(qs, "bc") or ""
    check_in = check_out = None
    bc_dates = re.search(r"o(\d{6})i(\d{6})", bc)
    if bc_dates:
        out_raw, in_raw = bc_dates.groups()
        check_in = _ddmmyy(out_raw)   # outbound flight = trip start
        check_out = _ddmmyy(in_raw)   # inbound flight = trip end
    if not offer_id:
        h = re.search(r"h(\d+)", bc)
        offer_id = h.group(1) if h else None

    # Occupancy: adult=2, child=[2,5] (an array of child ages -> count them).
    adults = _first(qs, "adult", "adults") or "2"
    child_raw = _first(qs, "child", "children") or ""
    child_ages = [a for a in re.findall(r"\d+", child_raw)]
    children = str(len(child_ages))

    # Departure airport lives under the bracketed key "airports[]".
    airport = _first(qs, "airports[]", "airports", "airport")

    extra = {k: v[0] for k, v in qs.items()}
    if airport:
        extra["departure_airport"] = airport

    return ParsedUrl(
        provider="holidayfinder",
        raw_url=url,
        destination=airport,  # only the origin airport is in the URL; real dest resolves via API
        check_in_date=check_in,
        check_out_date=check_out,
        room_config=_normalize_occupancy(adults, children),
        target_hotel_id_or_name=offer_id,
        extra=extra,
    )


def _ddmmyy(raw: str) -> date | None:
    """Convert a 'DDMMYY' string (e.g. '150926') into a date (2026-09-15)."""
    try:
        day, month, year = int(raw[0:2]), int(raw[2:4]), 2000 + int(raw[4:6])
        return date(year, month, day)
    except (ValueError, IndexError):
        return None


def _normalize_occupancy(adults: str, children: str, rooms: str | None = None) -> str:
    parts = [f"{adults}-adults"]
    if children and children != "0":
        parts.append(f"{children}-children")
    if rooms:
        parts.append(f"{rooms}-rooms")
    return ",".join(parts)
