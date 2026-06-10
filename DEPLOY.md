# Deploying TripStalker to the cloud (Render)

This repo ships a `render.yaml` blueprint that provisions the
**API (FastAPI)** and **Frontend (React)**. You bring a **PostgreSQL** (Render
allows only one free DB per account, so the blueprint doesn't create one) and
the daily check runs as a free **GitHub Action**.

## One-time setup

1. Push this repo to GitHub.
2. **Provision a Postgres** and copy its connection string — either:
   - a free **Neon** DB at <https://neon.tech> (recommended — fresh, no clashes), or
   - your existing Render free Postgres (only if it's empty — our tables include
     a `users` table that could collide with another app's).
3. Create a free account at <https://render.com> and connect your GitHub.
4. In Render: **New → Blueprint** → pick `tripStalker` → **Apply** → **Approve**.
   Render creates the API + static frontend.

## After the first deploy — set the env vars

These are marked `sync: false`, so set them once in each service's **Environment**:

| Service | Env var | Set it to |
|---------|---------|-----------|
| `tripstalker-api` | `DATABASE_URL` | your Postgres connection string (step 2) |
| `tripstalker-api` | `FRONTEND_ORIGIN` | the web URL, e.g. `https://tripstalker-web.onrender.com` |
| `tripstalker-web` | `VITE_API_BASE` | the API URL, e.g. `https://tripstalker-api.onrender.com` |

After setting them, trigger a redeploy of each service (the frontend must be
**rebuilt** because Vite bakes `VITE_API_BASE` in at build time).

Your live app is then the `tripstalker-web` URL — open it from any device. 🎉

## Daily price check — free, via GitHub Actions

Render Cron is paid, so the daily check runs as a **free GitHub Action**
(`.github/workflows/daily-price-check.yml`) that calls the API's
`/api/cron/check-prices` endpoint. Set two repo secrets so it can authenticate:

1. In Render, open the `tripstalker-api` service → **Environment** → copy the
   auto-generated **`CRON_SECRET`** value.
2. In GitHub: repo **Settings → Secrets and variables → Actions → New secret**,
   add two secrets:
   - `API_URL`     = your API URL, e.g. `https://tripstalker-api.onrender.com`
   - `CRON_SECRET` = the value copied from Render
3. (Optional) Test it now: repo **Actions → Daily price check → Run workflow**.

## Free-tier caveats (good to know)

- **Cold starts:** free web services sleep after ~15 min idle; the first request
  then takes a few seconds to wake (the Action allows up to 120s for this).
- **Database lifetime:** Render's free PostgreSQL is time-limited — back up or
  upgrade before it expires if you want to keep data.

## Alternatives

The same layout maps cleanly to **Railway** or **Fly.io** (backend + Postgres)
plus **Vercel/Netlify** for the static frontend — only the config format differs.
