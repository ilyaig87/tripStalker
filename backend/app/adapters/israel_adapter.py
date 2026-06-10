"""IsraelAdapter — Travelist.co.il (and a template for other local IL sites).

Strategy: Israeli OTAs rarely expose a public API, so we reverse-engineer the
internal endpoint the site's own frontend calls (visible in the browser's
Network tab as an XHR/fetch returning JSON). We replay that request from the
backend with proper headers and, if the site sits behind a WAF (Cloudflare /
Imperva), a residential proxy.

This file is a SKELETON with concrete reverse-engineering hints. The exact
endpoint + payload MUST be confirmed against a live session — see the numbered
steps in `_INTERNAL_API_HINTS` below before going live.
"""
from __future__ import annotations

from decimal import Decimal

import httpx

from app.adapters.base import BaseProviderAdapter, PriceResult, ProviderError
from app.config import settings
from app.url_parser import ParsedUrl

# ---------------------------------------------------------------------------
# REVERSE ENGINEERING PLAYBOOK (do this once, manually, then fill in below)
# ---------------------------------------------------------------------------
_INTERNAL_API_HINTS = """
1. Open a Travelist deal/search page in Chrome with DevTools -> Network -> Fetch/XHR.
2. Trigger a search (change dates / occupancy). Look for a JSON response that
   contains the price you see on screen. Common shapes:
       POST https://www.travelist.co.il/api/.../search
       GET  https://www.travelist.co.il/.../availability?dealId=...&checkIn=...
3. Right-click that request -> "Copy as cURL" to capture the EXACT:
       - method, full URL, query params / JSON body
       - required headers (User-Agent, Accept, Referer, x-requested-with,
         and any auth/csrf token or cookie)
4. Map the JSON response: find the field holding the total price and currency
   (e.g. data.packages[].price.amount). Put that path in `_extract_price`.
5. If you get 403/429 or a challenge page instead of JSON -> WAF. Route the
   request through `settings.proxy_url` (residential) and reuse a warmed cookie.
"""

# Fill these in from step 3 above. Defaults are placeholders.
_SEARCH_ENDPOINT = "https://www.travelist.co.il/api/search"  # TODO: confirm real path
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
    "Referer": "https://www.travelist.co.il/",
    "X-Requested-With": "XMLHttpRequest",
}


class IsraelAdapter(BaseProviderAdapter):
    provider_key = "travelist"

    async def fetch_current_price(self, parsed: ParsedUrl) -> PriceResult:
        payload = self._build_payload(parsed)
        proxies = settings.proxy_url or None

        try:
            async with httpx.AsyncClient(timeout=25, proxy=proxies, follow_redirects=True) as client:
                # Travelist's internal search is typically a POST with a JSON body;
                # switch to client.get(..., params=payload) if step 2 showed a GET.
                resp = await client.post(_SEARCH_ENDPOINT, headers=_DEFAULT_HEADERS, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(
                f"IsraelAdapter fetch failed ({exc}). If this is a 403/JSON error, "
                f"the WAF likely blocked us — configure PROXY_URL. See hints:\n{_INTERNAL_API_HINTS}"
            ) from exc

        return self._extract_price(data, parsed)

    # ------------------------------------------------------------- internals
    def _build_payload(self, parsed: ParsedUrl) -> dict:
        """Translate normalized params into Travelist's expected request body.

        The keys below are GUESSES based on common Israeli OTA APIs — replace
        them with the real ones captured in the playbook step 3.
        """
        return {
            "destination": parsed.destination,
            "checkIn": str(parsed.check_in_date) if parsed.check_in_date else None,
            "checkOut": str(parsed.check_out_date) if parsed.check_out_date else None,
            "rooms": _rooms_payload(parsed.room_config),
            "dealId": parsed.target_hotel_id_or_name,
            "currency": "ILS",
        }

    def _extract_price(self, data: dict, parsed: ParsedUrl) -> PriceResult:
        """Pull the price for the user's specific package out of the JSON.

        TODO: replace this traversal with the real path found in playbook step 4.
        We defensively try a few likely shapes so the skeleton fails loudly
        with a helpful message instead of a random KeyError.
        """
        candidates = (
            data.get("packages")
            or data.get("results")
            or data.get("deals")
            or []
        )
        if not candidates:
            raise ProviderError(
                "Could not locate price array in Travelist response. "
                "Inspect the JSON and update `_extract_price`."
            )

        # If we know the target deal, prefer it; otherwise take the cheapest.
        target_id = parsed.target_hotel_id_or_name

        def price_of(item: dict) -> float:
            node = item.get("price", item)
            return float(node.get("amount", node.get("total", float("inf"))))

        chosen = None
        if target_id:
            chosen = next(
                (c for c in candidates if str(c.get("id") or c.get("dealId")) == str(target_id)),
                None,
            )
        if chosen is None:
            chosen = min(candidates, key=price_of)

        price_node = chosen.get("price", chosen)
        amount = price_node.get("amount", price_node.get("total"))
        if amount is None:
            raise ProviderError("Found package but no price field — update `_extract_price`.")

        return PriceResult(
            price=Decimal(str(amount)).quantize(Decimal("1.00")),
            currency=price_node.get("currency", "ILS"),
            hotel_name=chosen.get("name") or chosen.get("hotelName"),
            raw=chosen,
        )


def _rooms_payload(room_config: str | None) -> list[dict]:
    adults, children = 2, 0
    for part in (room_config or "").split(","):
        if part.endswith("-adults"):
            adults = int(part.split("-")[0])
        elif part.endswith("-children"):
            children = int(part.split("-")[0])
    return [{"adults": adults, "children": children}]
