"""Reverse-engineer any travel site's price API.

Loads a page in a real headless browser, records every JSON/XHR response, and
reports which ones contain a price (and the JSON path to it) + the request
method/URL/params — so we can wire a server-side httpx adapter.

Usage:
    python scripts/capture_provider.py "<full offer/hotel URL>"
"""
import asyncio
import re
import sys
from urllib.parse import urlparse

from playwright.async_api import async_playwright

# Heuristic: a real price is a 3-6 digit number under a price-ish key.
PRICE_KEY = re.compile(r"(price|amount|total|cost|rate|sum)", re.I)
CURRENCY = re.compile(r"[\d,]{3,}\s*(?:₪|\$|€|USD|ILS|EUR)|(?:₪|\$|€)\s*[\d,]{3,}")


def find_prices(obj, path="$"):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (int, float)) and PRICE_KEY.search(k) and 100 <= v <= 200000:
                out.append((f"{path}.{k}", v))
            out += find_prices(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:10]):
            out += find_prices(v, f"{path}[{i}]")
    return out


async def main(url: str):
    host = urlparse(url).netloc
    api_hits = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(
            locale="he-IL",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        )
        page = await ctx.new_page()

        async def on_response(resp):
            ct = resp.headers.get("content-type", "")
            if "application/json" not in ct:
                return
            try:
                data = await resp.json()
            except Exception:
                return
            prices = find_prices(data)
            if prices:
                api_hits.append((resp.request.method, resp.url, prices))

        page.on("response", on_response)

        print(f"Loading {host} ...", file=sys.stderr)
        try:
            await page.goto(url, wait_until="networkidle", timeout=60000)
        except Exception as exc:  # networkidle can time out on busy pages — keep going
            print(f"  (goto warning: {exc})", file=sys.stderr)
        await page.wait_for_timeout(5000)  # let late XHRs settle

        body = await page.inner_text("body")
        visible = CURRENCY.findall(body) or re.findall(r"[\d,]{4,}\s*(?:₪|\$)", body)

        await browser.close()

    print("\n================ PRICE-BEARING API CALLS ================")
    if not api_hits:
        print("  (none found — price may be in HTML/text, or behind a WAF/login)")
    for method, u, prices in api_hits:
        print(f"\n  {method} {u}")
        for jpath, val in prices[:10]:
            print(f"      {jpath} = {val}")

    print("\n================ VISIBLE PRICES IN DOM ================")
    print("  ", sorted(set(visible))[:15] or "(none)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python scripts/capture_provider.py "<url>"', file=sys.stderr)
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
