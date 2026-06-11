"""Adapter Pattern base class.

Every provider (global API-based, or Israeli scraped) implements this contract.
The rest of the system only ever talks to `BaseProviderAdapter`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

from app.url_parser import ParsedUrl


@dataclass
class PriceResult:
    """Normalized price returned by every adapter."""
    price: Decimal
    currency: str = "USD"
    hotel_name: str | None = None      # resolved display name, if the provider gives one
    hotel_url: str | None = None       # the hotel's own website (direct-price fallback link)
    destination_city: str | None = None    # resolved destination city (for weather etc.)
    hotel_portion: Decimal | None = None   # package breakdown: hotel part (if known)
    flight_portion: Decimal | None = None  # package breakdown: flight part (if known)
    flight_details: str | None = None      # JSON: package flight legs (times, airline)
    hotel_meta: dict | None = None     # rich extras: stars, reviews, board, room, tags, photo, maps…
    raw: dict | None = None            # original provider payload, for debugging


class ProviderError(Exception):
    """Raised when an adapter cannot fetch a price (blocked, sold out, parse error...)."""


class BaseProviderAdapter(ABC):
    """Contract for all provider adapters."""

    #: provider key, must match the one produced by the url_parser (e.g. "booking")
    provider_key: str = "base"

    @abstractmethod
    async def fetch_current_price(self, parsed: ParsedUrl) -> PriceResult:
        """Return the current lowest price for the parsed search parameters.

        Implementations should raise `ProviderError` on failure rather than
        returning a sentinel, so the cron worker can log/skip gracefully.
        """
        raise NotImplementedError
