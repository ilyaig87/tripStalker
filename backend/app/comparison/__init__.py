"""Cross-provider price comparison.

Hotellook (hotels) shut down in 2025; the working free source is the
Travelpayouts/Aviasales FLIGHTS data API — a cheapest-fares "route radar".
"""
from app.comparison.flights import derive_route, fetch_offers

__all__ = ["derive_route", "fetch_offers"]
