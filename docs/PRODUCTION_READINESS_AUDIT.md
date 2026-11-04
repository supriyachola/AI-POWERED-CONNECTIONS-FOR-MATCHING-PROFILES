# Production readiness audit — Phase 1 status

Scope: the uploaded source snapshot (not a live penetration test). **Verified** = covered by an automated test that passes (backend tests run on SQLite, not Postgres). "Not browser-tested" = type-checks and builds, never exercised in a real browser.

| # | Sev | Finding (file) | Status |
|---|---|---|---|
| 1 | Critical | Frontend called ~15 routes that did not exist (history, blocked, friend requests, block/report, end, friendships) (`main.py`) | **Fixed, verified** (`tests/test_social.py`) |
| 2 | Critical | No WebSocket route existed; `/ws/connection/{id}` 404 | **Fixed, verified** (`tests/test_websocket.py`); real-browser media not tested |
| 3 | Critical | `POST /connections` called undefined `accepted_friend_request()` → every call attempt raised `NameError` | **Fixed, verified** |
| 4 | Critical | Fallback JWT secret `dev-secret-change-me` | **Fixed, verified** — production refuses weak/missing secret, SQLite, missing/`*` CORS (`config.py`, `tests/test_config.py`) |
| 5 | Critical | Callee never accepted: caller auto-entered the call; nothing moved a call to `active`; 20 s cleanup killed `calling` rows | **Fixed, verified** — state machine, explicit accept/reject/cancel, `active` only after both peers confirm media |
| 6 | High | Token in WebSocket URL | **Fixed, verified** — single-use 60 s tickets bound to user+room |
| 7 | High | No room authorisation | **Fixed, verified** — participants only, non-participants get 404/close, blocked pairs refused, Origin checked |
| 8 | High | Block did not end an active call | **Fixed, verified** |
| 9 | High | Matching ignored the user's own `preferred_*`, `min_age`, `max_age`; ladder ended in "any age" | **Fixed, verified** — hard constraints in SQL; only location widens |
| 10 | High | Abandoned reservation permanently excluded a candidate | **Fixed, verified** — only skip/decline (7 d) and completed calls (24 h) exclude |
| 11 | High | Candidate query loaded all profiles + N+1 | **Fixed** (SQL filter, bulk loads, 300-row cap). Not load-tested |
| 12 | High | No auth rate limiting / lockout; username-enumeration timing | **Fixed, verified** (in-memory, single worker) |
| 13 | High | Local ICE candidates dropped before socket open; no TURN; no ICE restart | **Fixed in code** (queue, restart, `/rtc/config` with per-user TURN credentials — credential maths verified). **Not browser-tested; TURN server not deployed** |
| 14 | Medium | Naive/aware datetime mix in stale-call cleanup | **Fixed** |
| 15 | Medium | `package-lock.json` out of sync (`npm ci` failed) | **Fixed** — `npm ci` passes |
| 16 | Medium | `tsc --noEmit` failed (`theme="dark"`) | **Fixed** — typecheck passes |
| 17 | Medium | WebSocket cleanup could be cancelled mid-way, leaving peer uninformed | **Fixed, verified** (found by test) |
| 18 | Medium | No health/readiness endpoints | **Fixed, verified** (`/`, `/health`, `/ready`) |
| 19 | Medium | No tests, no CI | **Partly fixed** — 66 backend tests, `ci.yml` written (not yet run on GitHub). No frontend tests, no Playwright |
| 20 | Medium | Frontend bundle > 500 kB (emoji picker) | Open |

## Still open (not implemented)
- Secure refresh-session architecture / HttpOnly cookies, logout revocation, password reset (access token still 7 days, in localStorage).
- Alembic migrations (`create_all` + ALTERs remain), Redis/shared presence and hub (single worker only), DB indexes beyond those on Connection/FriendRequest FK columns.
- Age assurance beyond self-declared 18+, moderation tooling for reports, account deletion/data export, retention jobs, privacy notice.
- `getStats()` call-quality monitoring, device selection/switching, mobile/background handling.
- Playwright E2E, WebRTC cross-network tests, backup/restore drill, monitoring/alerts, remaining docs from the master prompt.
- Exact age is still shown to other users (product decision pending; brackets recommended).
- `Connection` keeps one row per user pair and direction, so history is reconstructed from `InteractionHistory`. Revisit with Alembic.
