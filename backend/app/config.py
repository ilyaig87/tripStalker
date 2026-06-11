"""Application configuration.

Reads settings from environment variables (and an optional `.env` file).
Copy `.env.example` to `.env` and fill in real values for production.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Database ---
    # For a quick MVP we default to SQLite. For production point this at PostgreSQL/Supabase, e.g.:
    #   postgresql+psycopg://user:pass@host:5432/tripstalker
    database_url: str = "sqlite:///./tripstalker.db"

    # --- Global adapter (Booking / RapidAPI / Travelpayouts) ---
    rapidapi_key: str = ""
    rapidapi_host: str = "booking-com.p.rapidapi.com"
    travelpayouts_token: str = ""
    travelpayouts_marker: str = ""  # affiliate id, used in flight deeplinks
    unsplash_access_key: str = ""   # destination photos

    # --- Israel adapter (Travelist) ---
    # Optional residential proxy used to bypass WAF (e.g. http://user:pass@gateway:port)
    proxy_url: str = ""

    # --- Business logic ---
    # A price drop must be at least this fraction below the baseline to trigger a notification.
    price_drop_threshold: float = 0.02  # 2%

    # --- CORS ---
    frontend_origin: str = "http://localhost:5173"

    # --- Auth (email + password → JWT) ---
    # CHANGE THIS in production. Anyone with the secret can forge login tokens.
    jwt_secret: str = "dev-insecure-change-me"
    jwt_expire_minutes: int = 60 * 24 * 14  # 14 days

    # --- Cron auth (Vercel Cron sends "Authorization: Bearer <CRON_SECRET>") ---
    cron_secret: str = ""

    # --- Telegram notifications (free). Create a bot via @BotFather. ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
