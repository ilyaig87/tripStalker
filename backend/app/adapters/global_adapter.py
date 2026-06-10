"""GlobalAdapter — Booking.com / generic global hotel API.

MVP strategy: do NOT scrape Booking directly (heavy WAF/bot protection).
Instead call a third-party aggregator API (RapidAPI Booking, Travelpayouts,
Hotellook, etc.) to fetch the current lowest price.

This file ships a MOCK implementation that returns a deterministic, slightly
fluctuating price so the full pipeline is runnable end-to-end without API keys.
Swap `_mock_price` for `_live_price` once you wire a real key in `.env`.
"""
from __future__ import annotations

import hashlib
from decimal import Decimal

import httpx

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
            raw={"mock": True, "seed": seed},
        )

    # ------------------------------------------------------------------ live
    async def _live_price(self, parsed: ParsedUrl) -> PriceResult:
        """Example RapidAPI 'Booking.com' call. Adjust path/params to your plan.

        NOTE: each RapidAPI provider has its own schema — treat this as a template.
        """
        url = f"https://{settings.rapidapi_host}/v1/hotels/search"
        headers = {
            "X-RapidAPI-Key": settings.rapidapi_key,
            "X-RapidAPI-Host": settings.rapidapi_host,
        }
        params = {
            "dest_id": parsed.destination,
            "checkin_date": str(parsed.check_in_date),
            "checkout_date": str(parsed.check_out_date),
            "adults_number": _adults_from(parsed.room_config),
            "order_by": "price",
            "units": "metric",
            "locale": "en-gb",
            "currency": "USD",
        }
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(url, headers=headers, params=params)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:  # network or JSON error
            raise ProviderError(f"GlobalAdapter live fetch failed: {exc}") from exc

        results = data.get("result") or []
        if not results:
            raise ProviderError("No hotels returned for the given parameters")

        cheapest = min(results, key=lambda h: h.get("min_total_price", float("inf")))
        return PriceResult(
            price=Decimal(str(cheapest["min_total_price"])).quantize(Decimal("1.00")),
            currency=cheapest.get("currencycode", "USD"),
            hotel_name=cheapest.get("hotel_name"),
            raw=cheapest,
        )


def _adults_from(room_config: str | None) -> str:
    if not room_config:
        return "2"
    for part in room_config.split(","):
        if part.endswith("-adults"):
            return part.split("-")[0]
    return "2"
