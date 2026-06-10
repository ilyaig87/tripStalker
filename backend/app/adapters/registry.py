"""Routes a provider key (from the URL parser) to its adapter instance."""
from __future__ import annotations

from app.adapters.base import BaseProviderAdapter, ProviderError
from app.adapters.global_adapter import GlobalAdapter
from app.adapters.holidayfinder_adapter import HolidayFinderAdapter
from app.adapters.israel_adapter import TravelistAdapter

# Singleton instances are fine — adapters are stateless.
_REGISTRY: dict[str, BaseProviderAdapter] = {
    "booking": GlobalAdapter(),
    "travelist": TravelistAdapter(),
    "holidayfinder": HolidayFinderAdapter(),
}


def get_adapter(provider: str) -> BaseProviderAdapter:
    adapter = _REGISTRY.get(provider)
    if adapter is None:
        raise ProviderError(f"Unsupported provider: {provider!r}")
    return adapter


def supported_providers() -> list[str]:
    return list(_REGISTRY)
