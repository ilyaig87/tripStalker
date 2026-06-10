"""Shared async HTTP helper with retry/backoff for transient upstream failures.

Travel APIs (e.g. HolidayFinder/Travelyo) intermittently return 503/429 under
load. Retrying *transient* failures avoids falsely flagging an offer as
unavailable. Definitive errors (e.g. 404, or a non-JSON body) are NOT retried —
they propagate so the adapter can treat them as "sold out / removed".
"""
from __future__ import annotations

import asyncio

import httpx

# Status codes worth retrying (rate limit + transient server/gateway errors).
_TRANSIENT_STATUS = {429, 500, 502, 503, 504}
# Delay before each retry; total attempts = len + 1 (here: 3 attempts).
_BACKOFF_SECONDS = (0.5, 1.5)


async def _request_json(
    method: str,
    url: str,
    *,
    params=None,
    json=None,
    headers=None,
    proxy: str | None = None,
    timeout: float = 30,
):
    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=timeout, proxy=proxy, follow_redirects=True) as client:
        for attempt in range(len(_BACKOFF_SECONDS) + 1):
            try:
                resp = await client.request(method, url, params=params, json=json, headers=headers)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in _TRANSIENT_STATUS:
                    raise  # 4xx (other than 429) — definitive, don't retry
                last_exc = exc
            except httpx.TransportError as exc:  # includes TimeoutException
                last_exc = exc
            if attempt < len(_BACKOFF_SECONDS):
                await asyncio.sleep(_BACKOFF_SECONDS[attempt])
    assert last_exc is not None
    raise last_exc


async def get_json(url: str, *, params=None, headers=None, proxy: str | None = None, timeout: float = 30):
    return await _request_json("GET", url, params=params, headers=headers, proxy=proxy, timeout=timeout)


async def post_json(
    url: str, *, json=None, params=None, headers=None, proxy: str | None = None, timeout: float = 30
):
    return await _request_json("POST", url, params=params, json=json, headers=headers, proxy=proxy, timeout=timeout)
