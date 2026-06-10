# Deploying TripStalker to the cloud (Render)

This repo ships a `render.yaml` blueprint that provisions everything:
**API (FastAPI) · Frontend (React) · PostgreSQL · daily worker (cron)**.

## One-time setup

1. Push this repo to GitHub (see the main flow / `gh repo create`).
2. Create a free account at <https://render.com> and connect your GitHub.
3. In Render: **New → Blueprint** → pick the `TripStalker` repo → **Apply**.
   Render reads `render.yaml` and creates the database + services.

## After the first deploy — wire the two URLs

The frontend and backend live on different URLs, so two env vars are set
manually once (they're marked `sync: false` in the blueprint):

| Service | Env var | Set it to |
|---------|---------|-----------|
| `tripstalker-web` | `VITE_API_BASE` | the API URL, e.g. `https://tripstalker-api.onrender.com` |
| `tripstalker-api` | `FRONTEND_ORIGIN` | the web URL, e.g. `https://tripstalker-web.onrender.com` |

After setting them, trigger a redeploy of each service (the frontend must be
**rebuilt** because Vite bakes `VITE_API_BASE` in at build time).

Your live app is then the `tripstalker-web` URL — open it from any device. 🎉

## Free-tier caveats (good to know)

- **Cold starts:** free web services sleep after ~15 min idle; the first request
  then takes a few seconds to wake.
- **Database lifetime:** Render's free PostgreSQL is time-limited — back up or
  upgrade before it expires if you want to keep data.
- **Cron worker:** Render Cron Jobs are a **paid** feature. On the free plan you
  can drop the `tripstalker-worker` block and instead run the daily check via an
  external scheduler (e.g. GitHub Actions, cron-job.org hitting a small trigger
  endpoint) or manually with `python worker.py`.

## Alternatives

The same layout maps cleanly to **Railway** or **Fly.io** (backend + Postgres)
plus **Vercel/Netlify** for the static frontend — only the config format differs.
