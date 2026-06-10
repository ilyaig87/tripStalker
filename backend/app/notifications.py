"""Mock notification routine.

For the MVP we just log the alert. Swap `notify_price_drop` for a real
provider (SendGrid, Resend, Twilio, web-push) when ready — keep the signature.
"""
from __future__ import annotations

import logging
from decimal import Decimal

logger = logging.getLogger("tripstalker.notify")


def notify_price_drop(
    email: str,
    hotel_name: str | None,
    old_price: Decimal,
    new_price: Decimal,
    currency: str,
) -> None:
    drop = old_price - new_price
    pct = (drop / old_price * 100) if old_price else Decimal(0)
    logger.info(
        "PRICE DROP for %s | %s: %s -> %s %s (-%.1f%%)",
        email,
        hotel_name or "tracked trip",
        old_price,
        new_price,
        currency,
        pct,
    )
    # TODO: integrate a real email/push provider here.
    print(
        f"[NOTIFY] {email}: '{hotel_name or 'trip'}' dropped "
        f"{old_price} -> {new_price} {currency} (-{pct:.1f}%)"
    )
