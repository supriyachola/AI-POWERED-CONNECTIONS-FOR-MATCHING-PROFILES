# Deployment notes (Render API · Vercel frontend · Neon Postgres)

## Render (API)
Set these environment variables (see `backend/.env.example`): `APP_ENV=production`, `DATABASE_URL`, `JWT_SECRET` (≥ 32 chars), `CORS_ORIGINS`
(exact frontend origin(s), e.g. `https://affinityplus.vercel.app`). **The service now fails to boot if any is missing or weak** —
check the Render logs for the exact message after deploying.
Start command stays `uvicorn app.main:app --host 0.0.0.0 --port $PORT` with **one worker** (presence, rate limits, tickets and the
WebSocket hub are in-process). Free-tier cold starts drop sockets; the clients reconnect.
Health checks: `GET /health` (liveness), `GET /ready` (database).

## Vercel (frontend)
`VITE_API_URL` = the API's public URL. Build: `npm run build`.

## WebRTC relay (TURN)
Without TURN, some mobile/corporate networks cannot connect. Options: a managed provider, or coturn on a small VM with
`use-auth-secret` + `static-auth-secret=<TURN_SHARED_SECRET>`, TLS on 5349, UDP relay port range open. Then set `TURN_URLS`
and `TURN_SHARED_SECRET` on the API. `GET /rtc/config` returns `turn_configured: true` when active. Credentials are minted per
request and expire after 1 hour; never put the shared secret in the frontend.
(Managed providers that issue credentials through their own REST API need a small change to `routers/rtc.py`.)

## Release steps
1. Merge after CI is green. 2. Deploy API, then frontend (the frontend now requires the new routes). 3. Smoke test: register two
users in two browsers, call, accept, chat, hang up, block.
Rollback: redeploy the previous Render/Vercel build. This release adds nullable `connections` columns via `migrate_schema()`
(additive; old code ignores them) and rewrites legacy `calling`/`connected` statuses, so rolling back is safe.
