"""Reverse-engineer the real HolidayFinder (Travelyo) price endpoint.

Loads the offer page in a real headless browser, records every JSON/XHR
response, and reports which ones contain the price so we can wire the adapter.
"""
import asyncio
import json
import re
import sys

from playwright.async_api import async_playwright

URL = (
    "https://www.holidayfinder.co.il/offer/6606726?bc=m4d32h6606726c21o150926i200926"
    "st1:recommended:AI&adult=2&child=%5B2%5D&airports%5B%5D=TLV&position=0"
)

# Heuristic: a real package price is a 4-6 digit number; look for price-ish keys.
PRICE_KEY = re.compile(r"(price|amount|total|cost)", re.I)


def find_prices(obj, path="$"):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (int, float)) and PRICE_KEY.search(k) and 500 <= v <= 100000:
                out.append((f"{path}.{k}", v))
            out += find_prices(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:10]):
            out += find_prices(v, f"{path}[{i}]")
    return out


async def main():
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

        print(f"Loading offer page...", file=sys.stderr)
        await page.goto(URL, wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(4000)  # let late XHRs settle

        # Also try to read the visible price text from the DOM.
        body = await page.inner_text("body")
        visible = re.findall(r"[\d,]{4,}\s*₪|₪\s*[\d,]{4,}", body)

        await browser.close()

    print("\n================ PRICE-BEARING API CALLS ================")
    if not api_hits:
        print("  (none found — price may be in HTML/text only)")
    for method, url, prices in api_hits:
        print(f"\n  {method} {url}")
        for jpath, val in prices[:8]:
            print(f"      {jpath} = {val}")

    print("\n================ VISIBLE ₪ PRICES IN DOM ================")
    print("  ", sorted(set(visible))[:15] or "(none)")


if __name__ == "__main__":
    asyncio.run(main())
