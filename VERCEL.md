# Deploying TripStalker to Vercel

Vercel hosts the **React frontend** and the **FastAPI backend** (as a Python
serverless function). Because serverless has no persistent disk, the database
is an external **Neon Postgres** (free), and the daily worker becomes a
**Vercel Cron** that calls `/api/cron/check-prices`.

```
 Vercel project "tripstalker-web"  ──calls──▶  Vercel project "tripstalker-api"  ──▶  Neon Postgres
   (React static, root: frontend)               (FastAPI serverless, root: backend)
                                                        ▲
                                          Vercel Cron (daily) hits /api/cron/check-prices
```

## 1. Create the database (Neon)

1. Sign up at <https://neon.tech> (free) and create a project.
2. Copy the **connection string** (looks like
   `postgresql://user:pass@ep-xxx.neon.tech/neondb?sslmode=require`).
   Our backend auto-converts it to the `psycopg` driver, so paste it as-is.

## 2. Deploy the backend (FastAPI serverless)

1. <https://vercel.com> → **Add New → Project** → import the `tripStalker` repo.
2. Set **Root Directory = `backend`**.
3. Add Environment Variables:
   - `DATABASE_URL` = the Neon connection string
   - `CRON_SECRET`  = any random string (protects the cron endpoint)
   - `FRONTEND_ORIGIN` = the web URL (fill in after step 3) — or `*` to start
4. Deploy. Note the URL, e.g. `https://tripstalker-api.vercel.app`.
   `backend/vercel.json` routes all requests to the function and registers the
   daily cron (`0 8 * * *`).

> Test it: open `https://<api-url>/health` → `{"status":"ok",...}`.

## 3. Deploy the frontend (React)

1. **Add New → Project** → import the **same** repo again (second project).
2. Set **Root Directory = `frontend`** (Vercel auto-detects Vite).
3. Add Environment Variable:
   - `VITE_API_BASE` = the backend URL from step 2 (e.g. `https://tripstalker-api.vercel.app`)
4. Deploy. This URL is your live app — open it from any device. 🎉

Then go back to the backend project and set `FRONTEND_ORIGIN` to this web URL,
and redeploy the backend (for correct CORS).

## Notes / caveats

- **Vite bakes `VITE_API_BASE` at build time** — if you change it, redeploy the
  frontend.
- **Serverless time limit:** the free (Hobby) plan caps function runtime (~10s).
  Fine for a handful of tracked items; for large volumes, batch the cron.
- **Cron is free on Vercel** (Hobby allows daily crons) — unlike Render.
- The `render.yaml` in the repo is an alternative one-platform deploy; ignore it
  if you go the Vercel route.
