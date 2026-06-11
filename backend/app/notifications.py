"""Price-change notifications (drops AND rises).

Sends a Telegram message when configured (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID),
and always logs. A failed send never breaks the price-check loop.
"""
from __future__ import annotations

import logging
from decimal import Decimal

import httpx

from app.config import settings

logger = logging.getLogger("tripstalker.notify")


def notify_price_change(
    email: str,
    hotel_name: str | None,
    old_price: Decimal,
    new_price: Decimal,
    currency: str,
    link: str | None = None,
    alternative: dict | None = None,
) -> None:
    """Notify on a meaningful price move — DOWN (a deal) or UP (heads-up)."""
    delta = new_price - old_price            # negative = drop, positive = rise
    pct = (abs(delta) / old_price * 100) if old_price else Decimal(0)
    name = hotel_name or "ההצעה במעקב"
    rising = delta > 0

    logger.info("PRICE %s %s | %s: %s -> %s %s (%+.1f%%)",
                "RISE" if rising else "DROP", email, name, old_price, new_price, currency,
                float(delta / old_price * 100) if old_price else 0)

    header = "📈 <b>עליית מחיר</b>" if rising else "✈️ <b>ירידת מחיר!</b>"
    arrow = "↑" if rising else "−"
    lines = [
        header,
        f"🏨 {name}",
        f"💰 {old_price} ← {new_price} {currency}  ({arrow}{pct:.1f}%)",
    ]
    if link:
        lines.append(f'🔗 <a href="{link}">לצפייה בהצעה</a>')
    if alternative:
        lines.append(
            f"💡 זול יותר: {alternative.get('check_in')} – {alternative.get('check_out')} "
            f"ב-${alternative.get('price')} (חיסכון ${alternative.get('savings')})"
        )
        if alternative.get("url"):
            lines.append(f'🔗 <a href="{alternative["url"]}">לחלופה הזולה</a>')
    _send_telegram("\n".join(lines))


# Backward-compat alias: existing callers may still import notify_price_drop.
notify_price_drop = notify_price_change


def _send_telegram(text: str) -> bool:
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if not (token and chat_id):
        print(f"[NOTIFY] {text}")  # fallback when Telegram isn't configured
        return False
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
        return True
    except httpx.HTTPError as exc:
        logger.warning("Telegram send failed: %s", exc)
        return False


def send_test_message() -> dict:
    """Send a sample Telegram message to confirm the bot is wired up."""
    if not (settings.telegram_bot_token and settings.telegram_chat_id):
        return {"configured": False, "sent": False}
    sent = _send_telegram(
        "✅ <b>TripStalker</b>\nההתראות מוגדרות ועובדות! מעכשיו תקבל כאן הודעה בכל ירידת מחיר. ✈️"
    )
    return {"configured": True, "sent": sent}
