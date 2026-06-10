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

import time
import uuid
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import httpx

from app.adapters._http import get_json
from app.adapters.base import BaseProviderAdapter, PriceResult, ProviderError
from app.config import settings
from app.url_parser import ParsedUrl

_API_BASE = "https://www.holidayfinder.co.il/api_no_auth/package_search/hf-offer"
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
        return self._extract_price(data, parsed, luggage_tier=_luggage_tier(bc))

    def _extract_price(self, data: dict, parsed: ParsedUrl, luggage_tier: str | None = None) -> PriceResult:
        rate = (data.get("data") or {}).get("recommendedRate") or {}
        rate_include = rate.get("rateInclude") or {}
        total = rate_include.get("total_price")
        if total is None:
            raise ProviderError(
                "HolidayFinder response had no rateInclude.total_price "
                "(offer may be sold out or the API shape changed)"
            )

        # The base total_price always uses the "naked" (no-luggage) flight. If the
        # user selected a luggage tier in their link, add the difference between
        # that tier and the naked flight (both `price_with_markup_all_pax`, USD).
        luggage_added = 0
        if luggage_tier in {"withTrolley", "withCib", "withBoth"}:
            group = next(iter((rate.get("flightRateOptions") or {}).values()), {})
            naked = (group.get("naked") or {}).get("price_with_markup_all_pax")
            chosen = (group.get(luggage_tier) or {}).get("price_with_markup_all_pax")
            if isinstance(naked, (int, float)) and isinstance(chosen, (int, float)):
                luggage_added = chosen - naked

        final = total + luggage_added
        per_pax = rate_include.get("total_price_per_pax")
        hotel = (rate.get("hotel") or {}).get("name") or f"HolidayFinder offer #{parsed.target_hotel_id_or_name}"
        return PriceResult(
            price=Decimal(str(final)).quantize(Decimal("1.00")),
            currency="USD",  # the site quotes packages in USD
            hotel_name=hotel,
            raw={
                "base_total_price": total,
                "luggage_tier": luggage_tier,
                "luggage_added": luggage_added,
                "total_price_per_pax": per_pax,
            },
        )


def _luggage_tier(bc: str) -> str | None:
    """Pull the luggage tier from a booking code like '...st1:withTrolley:AI'."""
    parts = bc.split(":")
    return parts[1] if len(parts) >= 2 else None
