"""Vercel serverless entrypoint for the FastAPI backend.

Vercel's Python runtime serves the exposed ASGI `app`. The `vercel.json`
rewrites route every request here, so FastAPI sees the original path
(e.g. /api/track, /health, /api/cron/check-prices).
"""
import sys
from pathlib import Path

# Make the backend package importable when Vercel executes this file.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import init_db  # noqa: E402
from app.main import app  # noqa: E402  (exposed for the Vercel Python runtime)

# Vercel may not run ASGI lifespan events, so ensure tables exist on cold start.
# create_all is idempotent (CREATE TABLE IF NOT EXISTS).
init_db()
