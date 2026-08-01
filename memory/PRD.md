# Kinkology — PRD

## Original Problem Statement
Create a local, self-hostable web app that acts as a bridge from the internet to control the OSSM device (KinkyMakers/OSSM-hardware). The app communicates with the device via the BLE commands it accepts, and must allow a user a certain amount of time of control.

## Architecture
- **Frontend**: React (CRA + craco), TailwindCSS, dark "hardware control deck" UI. Fonts: Chivo / JetBrains Mono / IBM Plex Sans.
- **Backend**: FastAPI + MongoDB (motor). JWT admin auth (bcrypt + PyJWT).
- **Realtime**: WebSockets. One "host" (owner's browser holding the Web Bluetooth link) + many "control" clients (guests). Backend hub relays the *active* guest's commands to the host and enforces time/queue server-side (1s ticker).
- **BLE**: Web Bluetooth API in the owner's browser (Chrome/Edge/Opera). Uses the REAL OSSM firmware v3+ protocol:
  - Service `522b443a-4f53-534d-0001-420badbabe69`
  - Command char (write) `522b443a-4f53-534d-1000-420badbabe69`
  - State char (notify) `522b443a-4f53-534d-2000-420badbabe69`
  - Commands are UTF-8 strings: `set:{speed|stroke|depth|sensation|pattern}:0-100`, `go:strokeEngine`, `go:menu`, `set:speed:0` (stop). Validated server-side with the same regex as firmware.

## User Personas
- **Owner/Admin**: runs the bridge near the device, connects BLE, issues timed access codes, monitors the live queue, can emergency-stop or skip.
- **Guest**: opens a shared link/code, waits in queue, gets timed control via a mobile-first console.

## Core Requirements (static)
1. Admin JWT login.
2. Create/manage access codes granting N minutes.
3. Guests join via code, queued, one active controller at a time, server-enforced countdown.
4. Full control console: speed, depth, stroke, sensation, 7 patterns, start/stop.
5. Live queue view + owner emergency stop / skip.
6. Web Bluetooth relay to real OSSM device.

## Implemented (2026-07-29)
- Admin auth (login/logout/me), seeded admin, bcrypt+JWT. Verified via curl.
- Access code CRUD: create, list, revoke, +minutes, delete. Verified.
- Realtime hub: queue, promotion, server-side time accounting, expiry, safety stop between turns. Verified via WS test.
- Command relay with firmware-matching validation (invalid commands dropped). Verified.
- Web Bluetooth host hook (`useBleHost`) with real UUIDs, command write, state notifications. (Requires physical device + compatible browser — cannot run in cloud/headless.)
- UI: Landing (code entry), Admin login, Admin dashboard (device host panel + owner test console + live session monitor + code management), Guest control (waiting/active/ended/invalid states). Verified via screenshots.
- **Advanced Auto Programs** (guest console): 6 automated motion routines (Wave, Build-Up, Tease/Edge, Depth Pulse, Surge, Random) that drive the device via timed commands; stop on manual touch/STOP/turn end. Frontend-driven.
- **Owner Safety Limits**: min depth floor + max speed cap set in admin dashboard, stored in `settings` collection, ENFORCED SERVER-SIDE in the relay (`Hub.clamp_command`), surfaced live in the guest console (clamped sliders + lock notes). Verified 27/27 tests (iteration_3).
- **Deployment/self-host support**: added `websockets`+`wsproto` to requirements (uvicorn WS support), `Caddyfile` for single-origin HTTPS reverse proxy.
- **Admin 2FA (TOTP)**: authenticator-app two-factor for admin login with QR enrollment (pyotp + qrcode), 10 one-time backup recovery codes (stored hashed), two-step login (password → 6-digit/recovery), enable/disable in dashboard. Enforced via short-lived `mfa_token`. Verified 37/37 tests (iteration_4).
- **Brute-force lockout / rate-limiting**: MongoDB `login_attempts` keyed by `ip:scope:email`, 5 failures → 15-min lockout (HTTP 429 + Retry-After), cleared on success. Applied to login, 2fa-login, 2fa-setup-verify, 2fa-disable. Verified 44/44 tests (iteration_5).
- **Live telemetry overlay** (`/overlay`, public, OBS-ready): backend tracks current speed/stroke/depth/sensation (post-clamp) + motion run time + session time, broadcast over public WS `/api/ws/overlay` (+ GET `/api/overlay/state`). Page renders rolling SVG sparklines + run/session timers + controller + status; `?transparent=1` for OBS. Dashboard has copy/open link card. Verified 47/47 tests (iteration_6).

## Credentials
See `/app/memory/test_credentials.md`. Preview/dev admin (env-seeded): `admin@ossm.local` / `ossm-admin-2026`.
Self-hosted Docker has NO seeded admin — owner creates it via first-run setup.

## Implemented (2026-07-30)
- **Closed-loop target-HR controller** (replaces linear BPM→speed): HR Sync now takes a Target Heart Rate (BPM) and adjusts device speed via an integral controller — error = target−live BPM, speed nudged by `response`(%/s per BPM) each 1s, bounded by Min/Max Speed and rate-limited by Ramp Up/Down (%/s). Speeds up to reach the target, eases off to hold it. Safety cutoff still bypasses for instant stop; still capped by Max-Speed limit and streamed via owner_telemetry to the overlay. Presets (Gentle/Responsive/Intense) now tune response+ramp+caps. Controller simulation verified (70→hold 130 BPM: ramps to 100% then eases to ~77%); UI + presets render (screenshot).
- **Overlay HR "waiting for monitor" state**: the HR tile now shows "waiting for monitor…" when no BPM is streaming, so it's clearly present even before a monitor connects. (Backend HR→overlay path re-confirmed working in preview; if HR was missing on the user's deployment it was a stale Docker build predating the HR feature — needs `docker compose up -d --build`.)
- **Security hardening** (from external code review): (1) fixed a NameError crash in `POST /api/setup` (`setup_admin` now takes `request: Request`) — first-run setup would 500 after creating the admin; (2) CORS hardened — defaults to same-origin only, and `CORS_ORIGINS='*'` auto-disables credentials (closes reflected-origin CSRF); compose no longer hardcodes `*`; (3) auth cookies switched `SameSite=none`→`lax` (still httponly+secure); (4) `get_jwt_secret()` fails loudly if `JWT_SECRET` is missing or equals the placeholder; removed the real secret that had been shipped in `env.docker.example` (now `CHANGE_ME_run_openssl_rand_hex_32`); (5) IP-keyed brute-force lockout on public `GET /api/access/{code}` (5 invalid → 429, cleared by a valid code); (6) `parse_object_id()` → clean 404 (not 500) on malformed code ids. Verified 14/14 security pytest + frontend smoke (iteration_12); login/dashboard/CRUD/2FA-enroll/logs all regress clean. ACTION FOR USER: if they ever deployed with the old committed JWT secret, rotate `JWT_SECRET` (invalidates existing sessions).
- **One-tap HR sync presets**: Gentle / Responsive / Intense buttons in the Heart Rate Sync card set the six curve values (Resting/Peak BPM, Min/Max Speed, Ramp Up/Down) in one tap; the matching preset auto-highlights when the current config equals it. Verified: tapping Intense applied 70/140/30/100/60/60 and highlighted correctly (screenshot); build clean.
- **Configurable ramp up/down for HR sync**: HR-Sync now eases the device speed toward the BPM-derived goal instead of jumping. Two new saved settings — Ramp Up (%/s) and Ramp Down (%/s), defaults 25/50 — drive a 150ms ticker in the owner browser that steps the applied speed toward target (snaps when within one step), streaming each value via `owner_telemetry` so the overlay speed graph follows smoothly. The HR safety cutoff still bypasses the ramp for an INSTANT stop. Card shows live applied % and target. Ramp math unit-verified (0→60@25%/s≈2.4s, 100→20@50%/s≈1.65s); UI + defaults confirmed by screenshot.
- **HR Safety Cutoff (configurable)**: owner-set max BPM in Safety Limits (`hr_cutoff`, 0 = off, persisted in settings). Enforced SERVER-SIDE: when live BPM ≥ cutoff the backend force-stops the device (`set:speed:0` + `go:menu`), sets `hr_over`, and blocks all motion — `clamp_command` forces any guest `set:speed:*`/`go:strokeEngine` to `set:speed:0` until BPM recovers below cutoff-3 (hysteresis). Audit events `hr_cutoff_triggered`/`hr_cutoff_cleared`. Telemetry frame carries `hr_cutoff`+`hr_over`; overlay shows a pulsing "CUTOFF" badge and the owner HR-Sync engine also forces speed 0 while over. Verified via WS: trigger→host stop, guest speed forced to 0 while over, hysteresis recovery, persistence.
- **Sync to Heart Rate mode**: owner dashboard card (`HeartRateSync.jsx`) that maps live BPM → device speed via a configurable linear mapping (Resting BPM→Min Speed, Peak BPM→Max Speed), persisted in localStorage. While enabled it writes `set:speed:N` to the device over the owner's BLE path AND sends `{type:"owner_telemetry",speed}` over the host WS so the `/overlay` speed graph reflects it (backend handler updates `hub.telemetry.speed`). Always clamped to the owner Max-Speed safety limit. Fail-safe: auto-stops (speed 0) + disables if the HR monitor or device drops; toggle is disabled until both are connected. Verified: backend owner_telemetry→overlay speed (WS test), mapping math (incl. safety cap), and card render/gating. Live BLE-driven loop reviewed but not headless-testable (Web Bluetooth).
- **Heart-rate tracking on overlay**: owner links a Bluetooth heart-rate monitor via the standard BLE Heart Rate Service (0x180D / 0x2A37) using Web Bluetooth from the dashboard (`useHeartRate.js`). Works with chest straps/optical monitors directly and Apple Watch via a HR-broadcaster app (HeartCast/BlueHeart). Live BPM streams over authenticated WS `/api/ws/hr` → backend adds `hr_bpm`+`hr_connected` to the telemetry frame → public `/overlay` shows a BPM tile with pulsing heart + red sparkline (Sparkline gained a `max` prop). Dashboard Device Host bar has HR status pill + connect/disconnect. bpm clamped 0..300; resets on disconnect. Verified backend 6/6 + frontend live overlay update (iteration_11).
- **Audit & Activity Log**: session-level logging of BOTH security/admin events (login success/fail, account lockout, owner created, 2FA enable/disable, code create/revoke/extend/delete, limits & URL updates, emergency stop, session skip, log cleared) AND session/guest events (device connected/disconnected, guest joined queue, guest took control, turn ended + reason). No per-command logging (by design). Stored in `audit_logs` (indexed on ts desc + category) via `log_event()`. Endpoints: `GET /api/logs` (filters: category, q text-search on action/actor/target, start/end date, limit/skip + total), `DELETE /api/logs` (manual clear, keep-everything policy), `GET /api/logs/export?format=csv|json` (attachment download). Frontend: dashboard "Recent Activity" card (`RecentActivity.jsx`, auto-refresh 5s) + dedicated `/admin/logs` page (`Logs.jsx`) with filter bar, event table, CSV/JSON export, load-more pagination, and Clear All. Verified full suite 69/69 backend + 100% frontend (iteration_10).
- **Configurable Local + Global URLs (set on first login, editable later)**: first-run setup form now also collects `local_url` (owner/OBS overlay base) and `public_url` (remote-guest base). Stored in `settings` (`/api/settings` returns them; `PUT /api/settings/urls` updates). Dashboard "Base URLs" card edits them live. Guest share links build from `public_url` (fallback window.origin); overlay link builds from `local_url`. Caddy TLS domain is env-driven (`{$DOMAIN}` from `.env`). Verified full suite 59/59 backend + 100% frontend (iteration_9): guest link → `https://ChangeMe/c/<CODE>`, overlay → `http://localhost/overlay`.
- **First-run setup flow**: env-based admin seeding is now OPTIONAL (only when `ADMIN_EMAIL`+`ADMIN_PASSWORD` set — used in preview/dev). Self-hosted Docker leaves them unset, so the owner creates the admin account on first launch. Endpoints: `GET /api/setup/status`, `POST /api/setup` (min 8-char pw, 403 once set up). `AdminLogin.jsx` gates: checking → create-owner form (with URL fields) → login → 2FA. Verified via `/app/test_setup_flow.py` + `/app/backend/tests/test_setup_and_urls.py`.
- **Same-origin frontend**: `api.js` falls back to `window.location` for `WS_BASE`/`API` when `REACT_APP_BACKEND_URL` is empty (Docker/Caddy single-origin). Preview unchanged.
- **Complete Docker Compose stack** (`docker-compose.yml`): `mongo` (7.0, named volume), `backend` (FastAPI :8001), `web` (multi-stage React build → Caddy static + `/api` reverse proxy incl. WebSockets, auto HTTPS 80/443). Files: `frontend/Dockerfile`, `frontend/Caddyfile` (`{$DOMAIN}` + `http://localhost`), `frontend/.dockerignore`, `env.docker.example` (JWT_SECRET + DOMAIN), `DOCKER.md`. `restart: unless-stopped`. NOTE: Docker build/run NOT executed here (no docker daemon) — compose YAML + app code validated; user runs `docker compose up -d --build` on their Mac.

## Implemented (2026-07-29)

## Implemented (2026-07-31)
- **MuSe / Love Spouse toy compatibility (researched + documented)**: investigated adding direct support for Love Spouse-protocol vibrators (model 5390 and similar "Chinese BLE toy" hardware). Confirmed via Buttplug's own protocol-documentation issue that these toys use one-way BLE *advertisement broadcasts* (11-byte manufacturer data, company ID `0xFFF0`) rather than a connectable GATT service — so neither Web Bluetooth (browser, used for the OSSM link) nor Intiface Central can address one directly; this rules out a same-pattern in-browser bridge like `useBleHost`. Existing open-source ESP32 gateway firmwares (LS-Buttplug, LVS-Gateway) solve this by emulating a Lovense toy to Intiface on one side and broadcasting the Love Spouse packets on the other — since `useToys`/`ButtplugClient` already speaks generic Buttplug WS (not Intiface-specific), no new client code is needed once such a gateway is on the network. Documented the setup path in `ToysPanel.jsx` (in-app copy) and `README.md`; no protocol/bridge code added in this app.

## Backlog
- **P1**: Multi-device support; persistent queue across backend restarts; per-slider min/max safety caps set by owner.
- **P1**: Reconnect resilience for guest WS (auto-retry).
- **P2**: Usage history/audit log per code; scheduled/one-time codes; QR code for share links.
- **P2**: Funscript player relay (`stream:pos:time`) — firmware already supports it.

## Notes / Limitations
- Actual BLE motion control can ONLY be exercised with a real OSSM (firmware v3+) and a Web Bluetooth-capable browser (Chrome/Edge/Opera) physically near the device. Not testable in this cloud preview — protocol is implemented per firmware source.
