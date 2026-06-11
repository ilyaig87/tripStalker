"""HolidayFinderAdapter — holidayfinder.co.il (Israeli flight+hotel packages).

REVERSE-ENGINEERED (live, 2026-06) — REAL prices, no mock:
  * Platform: Travelyo white-label. Site displays prices in USD ($).
  * Price API (NO AUTH required):
        GET /api_no_auth/package_search/hf-offer/<bc>
            ?adult=<n>&child=<[ages]>&airports[]=<IATA>&lang=he&muid=<hex>&tt=<ms>
    where <bc> is the "booking code" from the offer URL's `bc` query param
    (it encodes the offer id + the exact dates).
  * Total package price:  data.recommendedRate.rateInclude.total_price   (USD)
    Per-person price:      data.recommendedRate.rateInclude.total_price_per_pax
  * `muid` is just a client fingerprint and `tt` a cache-buster — both can be
    generated; they are not tied to a server session.

Verified example: offer 6606726, 15–20 Sep 2026, 2 adults + 1 child, AI ->
    total_price = 3837 USD, per_pax = 1279 USD  (matches the on-site "$3,837").
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import httpx

from app.adapters._http import get_json
from app.adapters.base import BaseProviderAdapter, PriceResult, ProviderError
from app.config import settings
from app.url_parser import ParsedUrl

_API_BASE = "https://www.holidayfinder.co.il/api_no_auth/package_search/hf-offer"
_GRAPH_URL = "https://www.holidayfinder.co.il/api_no_auth/holiday_finder/hotel-graph/"
_LUGGAGE_TIERS = {"withTrolley", "withCib", "withBoth"}
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
    "Referer": "https://www.holidayfinder.co.il/",
    "X-Requested-With": "XMLHttpRequest",
}


class HolidayFinderAdapter(BaseProviderAdapter):
    provider_key = "holidayfinder"

    async def fetch_current_price(self, parsed: ParsedUrl) -> PriceResult:
        # The booking code + occupancy live in the original URL's query string.
        # We re-parse `raw_url` so this also works from the cron worker (which
        # only has the stored columns + raw_url, not the transient `extra`).
        qs = parse_qs(urlparse(parsed.raw_url).query)

        def q(key: str, default: str = "") -> str:
            return qs[key][0] if qs.get(key) else default

        bc = q("bc")
        if not bc:
            raise ProviderError("HolidayFinder URL is missing the `bc` booking code")

        params = {
            "adult": q("adult", "2"),
            "child": q("child", "[]"),          # e.g. "[2]" -> one child aged 2
            "airports[]": q("airports[]", "TLV"),
            "lang": "he",
            "muid": uuid.uuid4().hex,            # client fingerprint (not auth)
            "tt": str(int(time.time() * 1000)),  # cache-buster
        }

        try:
            data = await get_json(
                f"{_API_BASE}/{bc}",
                params=params,
                headers=_HEADERS,
                proxy=settings.proxy_url or None,
            )
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"HolidayFinder fetch failed: {exc}") from exc

        # The luggage choice is encoded in the bc as ...st<n>:<tier>:<board>.
        result = self._extract_price(data, parsed, luggage_tier=_luggage_tier(bc))

        # HF sometimes returns an "on-the-fly" response with a contaminated hotel
        # name and just one room photo. Hotel name + gallery are date-independent,
        # so enrich them from a cached date-variant of the SAME offer.
        meta = result.hotel_meta or {}
        photos = meta.get("photos") or ([meta["photo"]] if meta.get("photo") else [])
        if not result.hotel_name or len(photos) < 2:
            occupancy = {"adult": params["adult"], "child": params["child"], "airports[]": params["airports[]"]}
            name, gallery = await self._probe_hotel_details(bc, occupancy)
            if name and not result.hotel_name:
                result.hotel_name = name
            if len(gallery) > len(photos):
                result.hotel_meta = {**meta, "photos": gallery, "photo": gallery[0]}
        return result

    async def _probe_hotel_details(self, bc: str, occupancy: dict) -> tuple[str | None, list[str]]:
        """Resolve a clean hotel name + photo gallery by querying cached date
        variants of the same offer (same occupancy). Best-effort — ('', []) on fail."""
        import re

        name: str | None = None
        photos: list[str] = []
        for repl in ("o150926i200926", "o151226i201226", "o150127i200127"):
            cand = re.sub(r"o\d{6}i\d{6}", repl, bc)
            if cand == bc:
                continue
            params = {
                **occupancy, "lang": "he",
                "muid": uuid.uuid4().hex, "tt": str(int(time.time() * 1000)),
            }
            try:
                d = await get_json(f"{_API_BASE}/{cand}", params=params, headers=_HEADERS, proxy=settings.proxy_url or None)
            except (httpx.HTTPError, ValueError):
                continue
            h = (d.get("data") or {}).get("recommendedRate", {}).get("hotel") or {}
            nm = h.get("hotelName") or h.get("name")
            room = (h.get("selectedRoom") or {}).get("roomName")
            if nm and nm != room and not name:
                name = nm
            imgs = [im for im in (h.get("images") or []) if isinstance(im, str) and im.startswith("http")]
            if len(imgs) > len(photos):
                photos = imgs
            if name and len(photos) >= 3:
                break
        return name, photos[:10]

    def _extract_price(self, data: dict, parsed: ParsedUrl, luggage_tier: str | None = None) -> PriceResult:
        rate = (data.get("data") or {}).get("recommendedRate") or {}
        rate_include = rate.get("rateInclude") or {}
        total = rate_include.get("total_price")
        if total is None:
            raise ProviderError(
                "HolidayFinder response had no rateInclude.total_price "
                "(offer may be sold out or the API shape changed)"
            )

        # The base total_price always uses the "naked" (no-luggage) flight. We also
        # use the flight rate options to (a) add the user's luggage tier and (b)
        # break the package into hotel vs flight: the flight = the chosen tier's
        # `price_with_markup_all_pax` (USD, all pax); the hotel = total − naked flight.
        group = next(iter((rate.get("flightRateOptions") or {}).values()), {})
        naked = (group.get("naked") or {}).get("price_with_markup_all_pax")
        luggage_added = 0
        if luggage_tier in {"withTrolley", "withCib", "withBoth"}:
            chosen = (group.get(luggage_tier) or {}).get("price_with_markup_all_pax")
            if isinstance(naked, (int, float)) and isinstance(chosen, (int, float)):
                luggage_added = chosen - naked

        final = total + luggage_added
        hotel_portion = flight_portion = None
        if isinstance(naked, (int, float)):
            hotel_portion = Decimal(str(total - naked)).quantize(Decimal("1.00"))
            flight_portion = Decimal(str(naked + luggage_added)).quantize(Decimal("1.00"))

        per_pax = rate_include.get("total_price_per_pax")
        # The real HOTEL name. HF is inconsistent: for some dates the `name`/
        # `hotelName` field is contaminated with the selected ROOM name. Detect
        # that (name == the room) and return None so we never show a room as the
        # hotel — the caller keeps any previously-resolved real name instead.
        hotel_obj = rate.get("hotel") or {}
        raw_name = hotel_obj.get("hotelName") or hotel_obj.get("name")
        room_name = (hotel_obj.get("selectedRoom") or {}).get("roomName")
        hotel = raw_name if (raw_name and raw_name != room_name) else None
        dest_city = ((data.get("data") or {}).get("destination_data") or {}).get("name_en")

        cfd = rate.get("cheapest_flight_data") or {}
        legs = {"out": _hf_flight_leg(cfd), "back": _hf_flight_leg(cfd.get("default_inbound") or {})}
        flight_details = json.dumps(legs) if (legs["out"] or legs["back"]) else None

        hotel_url = hotel_obj.get("hotelWebsiteUrl") or None
        hotel_meta = _hf_hotel_meta(hotel_obj, cfd)
        return PriceResult(
            price=Decimal(str(final)).quantize(Decimal("1.00")),
            currency="USD",  # the site quotes packages in USD
            hotel_name=hotel,
            hotel_url=hotel_url,
            destination_city=dest_city,
            hotel_portion=hotel_portion,
            flight_portion=flight_portion,
            flight_details=flight_details,
            hotel_meta=hotel_meta,
            raw={
                "base_total_price": total,
                "luggage_tier": luggage_tier,
                "luggage_added": luggage_added,
                "total_price_per_pax": per_pax,
            },
        )


    async def find_cheaper_alternative(self, parsed: ParsedUrl, current_price: Decimal) -> dict | None:
        """Same hotel + same nights, cheaper on OTHER dates within the SAME month.

        Queries the hotel-graph for the tracked month only (keeps the same season),
        with the SAME luggage tier the user chose so prices match the tracked price.
        Returns the cheapest date that beats the user's own dates by a threshold;
        else None. Best-effort — never raises.
        """
        if not (parsed.check_in_date and parsed.check_out_date):
            return None
        qs = parse_qs(urlparse(parsed.raw_url).query)

        def q(key: str, default: str = "") -> str:
            return qs[key][0] if qs.get(key) else default

        bc = q("bc")
        parts = bc.split(":")
        if not parts[0]:
            return None
        hf_offer_id = parts[0]
        board = parts[2] if len(parts) >= 3 else "AI"
        tier = parts[1] if len(parts) >= 2 else "naked"
        luggage = tier if tier in _LUGGAGE_TIERS else "naked"
        duration = (parsed.check_out_date - parsed.check_in_date).days
        user_start = parsed.check_in_date.strftime("%d/%m/%Y")
        occupancy = {"adult": q("adult", "2"), "child": q("child", "[]"), "airports[]": q("airports[]", "TLV")}

        try:
            priced = await self._month_dates(
                hf_offer_id, board, luggage, duration,
                parsed.check_in_date.year, parsed.check_in_date.month, occupancy,
            )
        except (httpx.HTTPError, ValueError):
            return None
        if not priced:
            return None

        baseline = next((e["price"] for e in priced if e.get("start") == user_start), float(current_price))
        cheapest = min(priced, key=lambda e: e["price"])
        if cheapest.get("start") == user_start:
            return None  # the user's dates are already the cheapest this month
        savings = baseline - cheapest["price"]
        if savings < max(50, baseline * 0.03):  # only suggest a meaningful saving
            return None
        return {
            "price": cheapest["price"],
            "check_in": _ddmmyyyy_to_iso(cheapest.get("start")),
            "check_out": _ddmmyyyy_to_iso(cheapest.get("end")),
            "url": cheapest.get("packageDeeplinkUrl") or cheapest.get("packageDeeplinkUrlLegacy"),
            "savings": round(savings),
        }

    async def _month_dates(
        self, hf_offer_id: str, board: str, luggage: str, duration: int, year: int, month: int, occupancy: dict
    ) -> list[dict]:
        """Fetch one month of hotel-graph date→price points (priced entries only)."""
        data = {
            "currency": "USD",
            "hotelBoard": board,
            "when": {"month": month, "year": year, "duration": duration},
            "luggage": luggage,
            "hfOfferId": hf_offer_id,
        }
        params = {
            "data": json.dumps(data),
            **occupancy,
            "lang": "he",
            "muid": uuid.uuid4().hex,
            "tt": str(int(time.time() * 1000)),
        }
        graph = await get_json(_GRAPH_URL, params=params, headers=_HEADERS, proxy=settings.proxy_url or None)
        hotels = (graph.get("data") or {}).get("hotel") or {}
        if not hotels:
            return []
        dates = next(iter(hotels.values())).get("dates") or []
        return [e for e in dates if isinstance(e.get("price"), (int, float)) and e["price"] > 0]


def _hf_flight_leg(node: dict) -> dict | None:
    """Pull one flight leg (date, airline, takeoff/landing hour, stops) from a
    HolidayFinder cheapest_flight_data node."""
    escales = node.get("escales") or []
    if not escales:
        return None
    return {
        "date": node.get("takeoff_date_format"),
        "airline": (escales[0] or {}).get("company_name"),
        "dep": (escales[0] or {}).get("takeoff_hour"),
        "arr": (escales[-1] or {}).get("landing_hour"),
        "stops": node.get("nb_escales") or 0,
    }


def _hf_hotel_meta(hotel: dict, cfd: dict) -> dict | None:
    """Collect the rich extras HolidayFinder exposes about a hotel + its flight,
    into a compact dict for display. Returns None if nothing useful was found."""
    meta: dict = {}

    stars = hotel.get("rating")
    if isinstance(stars, (int, float)) and stars:
        meta["stars"] = int(stars)

    rev = hotel.get("trustYouReviews") or {}
    if isinstance(rev.get("reviewScore"), (int, float)):
        meta["review_score"] = round(float(rev["reviewScore"]), 1)
        meta["review_count"] = rev.get("reviewCount")

    room = hotel.get("selectedRoom") or {}
    if room.get("roomName"):
        meta["room"] = room["roomName"]
    board = (hotel.get("room_board") or "").strip()
    if board:
        meta["board"] = board

    if hotel.get("refundable") and hotel.get("refundableUntil"):
        meta["refundable_until"] = hotel["refundableUntil"]
    meta["free_cancellation"] = bool(hotel.get("has_free_cancellation_option"))

    tags = hotel.get("hotelTags") or []  # prefer the Hebrew tags for an RTL UI
    if isinstance(tags, list) and tags:
        meta["tags"] = [t for t in tags if isinstance(t, str)][:5]

    if hotel.get("google_maps_url"):
        meta["maps_url"] = hotel["google_maps_url"]
    if hotel.get("packageHighlightOnCard"):
        meta["highlight"] = hotel["packageHighlightOnCard"].strip()

    images = hotel.get("images") or []
    if isinstance(images, list):
        photos = [im for im in images if isinstance(im, str) and im.startswith("http")][:10]
        if photos:
            meta["photos"] = photos
            meta["photo"] = photos[0]  # single, kept for back-compat

    # flight at-a-glance
    if cfd.get("flight_type"):
        meta["flight_kind"] = cfd["flight_type"]  # "charter" | "regular"
    if cfd.get("company_name"):
        meta["airline"] = cfd["company_name"]
    if cfd.get("flight_type_string"):
        meta["flight_label"] = cfd["flight_type_string"]  # e.g. "טיסה ישירה"

    return meta or None


def _luggage_tier(bc: str) -> str | None:
    """Pull the luggage tier from a booking code like '...st1:withTrolley:AI'."""
    parts = bc.split(":")
    return parts[1] if len(parts) >= 2 else None


def _ddmmyyyy_to_iso(value: str | None) -> str | None:
    """'01/09/2026' -> '2026-09-01'."""
    if not value:
        return None
    try:
        d, m, y = value.split("/")
        return date(int(y), int(m), int(d)).isoformat()
    except (ValueError, AttributeError):
        return None
