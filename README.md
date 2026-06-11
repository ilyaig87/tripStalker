# TripStalker — Global & Local Travel Price Tracker (MVP)

Track hotel & vacation-package prices from any supported travel link. Paste a
URL + email, and a daily worker checks for price drops and notifies you.

Supports **global** providers (Booking.com via API) and **Israeli local**
providers (Travelist.co.il via reverse-engineered internal API) through a clean
**Adapter Pattern**.

```
TripStalker/
├── backend/                  # FastAPI + SQLAlchemy
│   ├── app/
│   │   ├── main.py           # REST endpoints
│   │   ├── config.py         # env-based settings
│   │   ├── database.py       # engine / session / Base
│   │   ├── models.py         # users, tracked_items, price_history
│   │   ├── schemas.py        # Pydantic request/response
│   │   ├── crud.py           # DB helpers
│   │   ├── url_parser.py     # URL Parser Engine (routes domain -> provider)
│   │   ├── notifications.py  # mock price-drop alert
│   │   └── adapters/
│   │       ├── base.py           # BaseProviderAdapter (ABC)
│   │       ├── global_adapter.py # Booking/RapidAPI (mock + live template)
│   │       ├── israel_adapter.py # Travelist (reverse-engineering skeleton)
│   │       └── registry.py       # provider -> adapter routing
│   ├── worker.py             # daily cron job (the "Scraper Engine")
│   └── requirements.txt
└── frontend/                 # React + Vite + TypeScript + Tailwind
    └── src/{App.tsx, api.ts, main.tsx}
```

## Quickstart

> **Requires Python 3.10+** (developed on 3.13). The SQLAlchemy models use
> `str | None` type syntax that older Pythons can't evaluate at runtime.

### Backend
```bash
cd backend
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                 # works out-of-the-box; HolidayFinder uses real prices
uvicorn app.main:app --reload        # http://localhost:8000  (docs at /docs)
```

> The reverse-engineering helper `scripts/capture_holidayfinder.py` additionally
> needs Playwright (`pip install playwright && python -m playwright install chromium`).
> It is a one-off dev tool — the runtime adapters use plain `httpx`, no browser.

### Run the daily price check manually
```bash
cd backend && source .venv/bin/activate
python worker.py
```
Schedule it via cron (8am daily):
```
0 8 * * *  cd /path/to/TripStalker/backend && /path/to/.venv/bin/python worker.py
```

### Frontend
```bash
cd frontend
npm install
npm run dev                          # http://localhost:5173
```

## API
Auth is email + password → a JWT bearer token. Send it as
`Authorization: Bearer <token>` on every track endpoint; the owner is taken from
the token (never from the request body).

| Method | Path                     | Auth | Description                          |
|--------|--------------------------|------|--------------------------------------|
| POST   | `/api/auth/register`     | —    | Create account (`email`, `password`) → token |
| POST   | `/api/auth/login`        | —    | Sign in (`email`, `password`) → token |
| GET    | `/api/auth/me`           | ✓    | Current user                         |
| POST   | `/api/track`             | ✓    | Register a track (`url`)             |
| GET    | `/api/user/tracks`       | ✓    | List my tracks                       |
| POST   | `/api/user/refresh`      | ✓    | Re-check all my tracks now           |
| GET    | `/api/track/{id}`        | ✓    | Track detail + price history (owner) |
| POST   | `/api/track/{id}/reset`  | ✓    | Re-baseline to current price (owner) |
| DELETE | `/api/track/{id}`        | ✓    | Stop tracking (owner)                |
| GET    | `/health`                | —    | Health + supported providers         |

> Set `JWT_SECRET` to a long random value in production (`openssl rand -hex 32`).

## Price comparison & extras
- **Flight price radar (free):** set `TRAVELPAYOUTS_TOKEN` (+ optional
  `TRAVELPAYOUTS_MARKER`) in `.env` to enable a "cheapest fares recently seen on
  this route, by source" radar on Travelist (flight) tracks. Best-effort and
  clearly labeled as cached, not live. Validate with
  `python scripts/spike_flights.py TLV BCN`. (Hotellook hotel comparison was shut
  down in 2025, so cross-provider *hotel* prices are not available for free.)
- **Hotel/flight extras:** HolidayFinder packages surface stars, reviews, board,
  room, tags and the **flight luggage tier** (none / trolley / checked bag) via
  `hotel_meta`, shown on the card.

## MVP notes / going live
- **Mock mode:** with no API keys the `GlobalAdapter` returns deterministic
  fluctuating prices so the whole pipeline is demoable end-to-end.
- **Going live (Booking):** set `RAPIDAPI_KEY` in `.env` → `_live_price` activates.
- **Going live (Travelist):** follow the reverse-engineering playbook documented
  at the top of `adapters/israel_adapter.py`, fill in the real endpoint, request
  payload keys, and JSON price path; add `PROXY_URL` if the WAF blocks you.
- **Database:** defaults to SQLite; point `DATABASE_URL` at PostgreSQL/Supabase
  for production (uncomment `psycopg` in requirements).
