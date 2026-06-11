"""GlobalAdapter — Booking.com / generic global hotel API.

MVP strategy: do NOT scrape Booking directly (heavy WAF/bot protection).
Instead call a third-party aggregator API (RapidAPI Booking, Travelpayouts,
Hotellook, etc.) to fetch the current lowest price.

With `RAPIDAPI_KEY` set it calls the apidojo "Booking.com" API for real prices
(`_live_price`); without a key it returns a deterministic mock so the pipeline
is still runnable end-to-end. The switch is automatic in `fetch_current_price`.
"""
from __future__ import annotations

import hashlib
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import httpx

from app.adapters._http import get_json
from app.adapters.base import BaseProviderAdapter, PriceResult, ProviderError
from app.config import settings
from app.url_parser import ParsedUrl


class GlobalAdapter(BaseProviderAdapter):
    provider_key = "booking"

    async def fetch_current_price(self, parsed: ParsedUrl) -> PriceResult:
        if settings.rapidapi_key:
            return await self._live_price(parsed)
        return self._mock_price(parsed)

    # ------------------------------------------------------------------ mock
    def _mock_price(self, parsed: ParsedUrl) -> PriceResult:
        """Deterministic pseudo-price derived from the URL + a tiny daily wobble.

        Keeps the demo realistic: re-running it yields small ups/downs so the
        "price drop" logic in the worker actually fires sometimes.
        """
        seed = f"{parsed.target_hotel_id_or_name}|{parsed.check_in_date}|{parsed.room_config}"
        digest = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
        base = 150 + (digest % 350)            # base price between 150 and 500
        wobble = (digest >> 8) % 40 - 20       # +/- 20 fluctuation
        price = Decimal(base + wobble).quantize(Decimal("1.00"))
        return PriceResult(
            price=price,
            currency="USD",
            hotel_name=parsed.target_hotel_id_or_name or parsed.destination,
            destination_city=parsed.destination,
            raw={"mock": True, "seed": seed},
        )

    # ------------------------------------------------------------------ live
    async def _live_price(self, parsed: ParsedUrl) -> PriceResult:
        """Real Booking price via RapidAPI (apidojo 'Booking.com', USD).

        Flow: resolve a destination id (from the URL's dest_id, or by looking up
        the city name) -> search hotels for the dates/occupancy -> pick the user's
        specific hotel (matched by the URL slug) or the cheapest one.
        """
        host = settings.rapidapi_host
        headers = {"X-RapidAPI-Key": settings.rapidapi_key, "X-RapidAPI-Host": host}
        proxy = settings.proxy_url or None
        qs = parse_qs(urlparse(parsed.raw_url).query)

        def q(*keys: str, default: str | None = None) -> str | None:
            for k in keys:
                if qs.get(k):
                    return qs[k][0]
            return default

        checkin = q("checkin", "checkin_date") or (str(parsed.check_in_date) if parsed.check_in_date else None)
        checkout = q("checkout", "checkout_date") or (str(parsed.check_out_date) if parsed.check_out_date else None)
        if not (checkin and checkout):
            raise ProviderError("Booking URL is missing check-in/check-out dates")
        adults = q("group_adults", "adults", "adults_number") or _adults_from(parsed.room_config)

        # 1) Resolve the destination id.
        dest_id = q("dest_id")
        dest_type = q("dest_type", default="city")
        if not dest_id:
            city = q("ss", "city", "dest") or parsed.destination
            if not city:
                raise ProviderError("Could not determine Booking destination from the URL")
            try:
                locs = await get_json(
                    f"https://{host}/v1/hotels/locations",
                    params={"name": city, "locale": "en-gb"},
                    headers=headers,
                    proxy=proxy,
                )
            except (httpx.HTTPError, ValueError) as exc:
                raise ProviderError(f"Booking location lookup failed: {exc}") from exc
            match = next((l for l in locs if l.get("dest_type") == "city"), locs[0] if locs else None)
            if not match:
                raise ProviderError(f"Booking has no destination matching {city!r}")
            dest_id, dest_type = match["dest_id"], match.get("dest_type", "city")

        # 2) Search hotels.
        params = {
            "dest_id": dest_id,
            "dest_type": dest_type,
            "checkin_date": checkin,
            "checkout_date": checkout,
            "adults_number": adults,
            "room_number": "1",
            "order_by": "price",
            "filter_by_currency": "USD",
            "locale": "en-gb",
            "units": "metric",
            "page_number": "0",
        }
        try:
            data = await get_json(f"https://{host}/v1/hotels/search", params=params, headers=headers, proxy=proxy)
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"Booking search failed: {exc}") from exc

        results = [h for h in (data.get("result") or []) if h.get("min_total_price") is not None]
        if not results:
            raise ProviderError("No Booking hotels available for the given dates")

        # 3) Prefer the user's specific hotel (slug -> name match); else cheapest.
        slug = (parsed.target_hotel_id_or_name or "").replace("-", " ").lower()
        chosen = None
        if slug:
            chosen = next((h for h in results if slug in (h.get("hotel_name") or "").lower()), None)
        if chosen is None:
            chosen = min(results, key=lambda h: h["min_total_price"])

        return PriceResult(
            price=Decimal(str(chosen["min_total_price"])).quantize(Decimal("1.00")),
            currency="USD",
            hotel_name=chosen.get("hotel_name"),
            raw={"hotel_id": chosen.get("hotel_id"), "matched_specific": chosen is not None and bool(slug)},
        )


def _adults_from(room_config: str | None) -> str:
    if not room_config:
        return "2"
    for part in room_config.split(","):
        if part.endswith("-adults"):
            return part.split("-")[0]
    return "2"
