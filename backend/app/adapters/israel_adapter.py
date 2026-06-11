"""TravelistAdapter — travelist.co.il flight search (reverse-engineered, real).

Flow (no auth, no WAF, replayable with plain httpx):
  1. POST /api/v1/flights/startsearch  with the search params
        -> {"url": "https://www.travelist.co.il/search-results/<date>/<uuid>.json"}
  2. GET that url (the search runs async; poll until `products` appear)
        -> products[] each with `USDPrice` (total round-trip, USD)

We track the **cheapest** round-trip total (min USDPrice across products) in USD.
The search params are re-parsed from the stored `raw_url`, so the cron worker
works without the transient parsed `extra`.
"""
from __future__ import annotations

import asyncio
import re
import time
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import httpx

from app.adapters._http import get_json, post_json
from app.adapters.base import BaseProviderAdapter, PriceResult, ProviderError
from app.config import settings
from app.url_parser import ParsedUrl

_START_URL = "https://www.travelist.co.il/api/v1/flights/startsearch"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
    "Content-Type": "application/json",
    "Referer": "https://www.travelist.co.il/flightsResults",
    "X-Requested-With": "XMLHttpRequest",
}
_SEGMENT_RE = re.compile(r"segmentsClient\[(\d+)\]\[(from|to|date)\]")


class TravelistAdapter(BaseProviderAdapter):
    provider_key = "travelist"

    async def fetch_current_price(self, parsed: ParsedUrl) -> PriceResult:
        qs = parse_qs(urlparse(parsed.raw_url).query)
        segments = self._segments(qs)
        if not segments:
            raise ProviderError("Travelist URL has no flight segments (expected a /flightsResults link)")

        def as_int(key: str, default: int) -> int:
            try:
                return int(qs[key][0])
            except (KeyError, ValueError, IndexError):
                return default

        body = {
            "segmentsClient": segments,
            "flightType": (qs.get("flightType") or ["RoundTrip"])[0],
            "adults": as_int("adults", 2),
            "infants": as_int("infants", 0),
            "children": as_int("children", 0),
            "seniors": as_int("seniors", 0),
            "platform": "web",
            "deviceType": "desktop",
            "initStartTimestamp": int(time.time() * 1000),
        }
        proxy = settings.proxy_url or None

        try:
            start = await post_json(_START_URL, json=body, headers=_HEADERS, proxy=proxy)
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"Travelist startsearch failed: {exc}") from exc

        results_url = (start or {}).get("url")
        if not results_url:
            raise ProviderError("Travelist startsearch returned no results url")

        data = await self._poll_results(results_url, proxy)
        return self._extract_price(data, parsed)

    def _segments(self, qs: dict[str, list[str]]) -> list[dict[str, str]]:
        segs: dict[int, dict[str, str]] = {}
        for key, vals in qs.items():
            m = _SEGMENT_RE.fullmatch(key)
            if m and vals:
                segs.setdefault(int(m.group(1)), {})[m.group(2)] = vals[0]
        return [segs[i] for i in sorted(segs)]

    async def _poll_results(self, url: str, proxy: str | None, attempts: int = 8, delay: float = 1.5) -> dict:
        """The results JSON is written asynchronously — poll until it has products."""
        last: dict | None = None
        for _ in range(attempts):
            try:
                data = await get_json(url, headers=_HEADERS, proxy=proxy)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:  # not ready yet
                    await asyncio.sleep(delay)
                    continue
                raise ProviderError(f"Travelist results fetch failed: {exc}") from exc
            except (httpx.HTTPError, ValueError) as exc:
                raise ProviderError(f"Travelist results fetch failed: {exc}") from exc

            if data.get("products"):
                last = data
                progress = data.get("progress")
                if progress is None or (isinstance(progress, (int, float)) and progress >= 100):
                    return data
            await asyncio.sleep(delay)

        if last:
            return last
        raise ProviderError("Travelist returned no flight results in time")

    async def find_cheaper_alternative(self, parsed: ParsedUrl, current_price: Decimal) -> dict | None:
        """Cheapest round-trip fare for the same route in the tracked month,
        via the Travelpayouts (Aviasales) flight-data API. The price is an
        indicative "from" fare per person, so no savings figure is computed.
        Best-effort — returns None if not configured or nothing found.
        """
        token = settings.travelpayouts_token
        if not (token and parsed.check_in_date):
            return None
        segments = self._segments(parse_qs(urlparse(parsed.raw_url).query))
        if not segments:
            return None
        origin, dest = segments[0].get("from"), segments[0].get("to")
        if not (origin and dest):
            return None

        month = parsed.check_in_date.strftime("%Y-%m")
        params = {
            "origin": origin,
            "destination": dest,
            "departure_at": month,
            "return_at": month,
            "one_way": "false",
            "unique": "true",
            "sorting": "price",
            "limit": "1",
            "currency": "usd",
            "token": token,
        }
        try:
            data = await get_json(
                "https://api.travelpayouts.com/aviasales/v3/prices_for_dates",
                params=params,
                proxy=settings.proxy_url or None,
            )
        except (httpx.HTTPError, ValueError):
            return None

        rows = (data or {}).get("data") or []
        if not rows:
            return None
        best = rows[0]
        price = best.get("price")
        if price is None:
            return None

        link = best.get("link") or f"/search/{origin}{dest}1"
        url = f"https://www.aviasales.com{link}"
        if settings.travelpayouts_marker:
            url += f"{'&' if '?' in url else '?'}marker={settings.travelpayouts_marker}"
        return {
            "price": price,
            "check_in": (best.get("departure_at") or "")[:10] or None,
            "check_out": (best.get("return_at") or "")[:10] or None,
            "url": url,
            "savings": None,  # indicative per-person "from" fare — not directly comparable
            "details": {
                "airline": best.get("airline"),
                "transfers": best.get("transfers"),
                "return_transfers": best.get("return_transfers"),
                "departure_at": best.get("departure_at"),
                "return_at": best.get("return_at"),
                "duration_to": best.get("duration_to"),
                "duration_back": best.get("duration_back"),
            },
        }

    def _extract_price(self, data: dict, parsed: ParsedUrl) -> PriceResult:
        prices = []
        for product in data.get("products") or []:
            usd = product.get("USDPrice")
            if usd is None and product.get("agencies"):
                usd = (product["agencies"][0] or {}).get("USDPrice")
            if isinstance(usd, (int, float)) and usd > 0:
                prices.append(usd)
        if not prices:
            raise ProviderError("No priced flights in Travelist results (sold out / no availability)")

        cheapest = min(prices)
        label = parsed.target_hotel_id_or_name or parsed.destination or "טיסה"
        return PriceResult(
            price=Decimal(str(cheapest)).quantize(Decimal("1.00")),
            currency="USD",
            hotel_name=f"✈️ {label}",
            raw={"cheapest_usd": cheapest, "products": len(data.get("products") or [])},
        )
