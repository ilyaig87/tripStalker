"""Price-drop notifications.

Sends a Telegram message when configured (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID),
and always logs. A failed send never breaks the price-check loop.
"""
from __future__ import annotations

import logging
from decimal import Decimal

import httpx

from app.config import settings

logger = logging.getLogger("tripstalker.notify")


def notify_price_drop(
    email: str,
    hotel_name: str | None,
    old_price: Decimal,
    new_price: Decimal,
    currency: str,
    link: str | None = None,
) -> None:
    drop = old_price - new_price
    pct = (drop / old_price * 100) if old_price else Decimal(0)
    name = hotel_name or "ההצעה במעקב"

    logger.info("PRICE DROP %s | %s: %s -> %s %s (-%.1f%%)", email, name, old_price, new_price, currency, pct)

    lines = [
        "✈️ <b>ירידת מחיר!</b>",
        f"🏨 {name}",
        f"💰 {old_price} ← {new_price} {currency}  (−{pct:.1f}%)",
    ]
    if link:
        lines.append(f'🔗 <a href="{link}">לצפייה בהצעה</a>')
    _send_telegram("\n".join(lines))


def _send_telegram(text: str) -> None:
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if not (token and chat_id):
        print(f"[NOTIFY] {text}")  # fallback when Telegram isn't configured
        return
    try:
        resp = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Telegram send failed: %s", exc)
