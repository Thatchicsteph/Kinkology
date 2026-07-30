# OSSM Bridge — PRD

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
- **Configurable Local + Global URLs (set on first login, editable later)**: first-run setup form now also collects `local_url` (owner/OBS overlay base) and `public_url` (remote-guest base). Stored in `settings` (`/api/settings` returns them; `PUT /api/settings/urls` updates). Dashboard "Base URLs" card edits them live. Guest share links build from `public_url` (fallback window.origin); overlay link builds from `local_url`. Caddy TLS domain is env-driven (`{$DOMAIN}` from `.env`). Verified full suite 59/59 backend + 100% frontend (iteration_9): guest link → `https://tg30.ddns.net/c/<CODE>`, overlay → `http://localhost/overlay`.
- **First-run setup flow**: env-based admin seeding is now OPTIONAL (only when `ADMIN_EMAIL`+`ADMIN_PASSWORD` set — used in preview/dev). Self-hosted Docker leaves them unset, so the owner creates the admin account on first launch. Endpoints: `GET /api/setup/status`, `POST /api/setup` (min 8-char pw, 403 once set up). `AdminLogin.jsx` gates: checking → create-owner form (with URL fields) → login → 2FA. Verified via `/app/test_setup_flow.py` + `/app/backend/tests/test_setup_and_urls.py`.
- **Same-origin frontend**: `api.js` falls back to `window.location` for `WS_BASE`/`API` when `REACT_APP_BACKEND_URL` is empty (Docker/Caddy single-origin). Preview unchanged.
- **Complete Docker Compose stack** (`docker-compose.yml`): `mongo` (7.0, named volume), `backend` (FastAPI :8001), `web` (multi-stage React build → Caddy static + `/api` reverse proxy incl. WebSockets, auto HTTPS 80/443). Files: `frontend/Dockerfile`, `frontend/Caddyfile` (`{$DOMAIN}` + `http://localhost`), `frontend/.dockerignore`, `env.docker.example` (JWT_SECRET + DOMAIN), `DOCKER.md`. `restart: unless-stopped`. NOTE: Docker build/run NOT executed here (no docker daemon) — compose YAML + app code validated; user runs `docker compose up -d --build` on their Mac.

## Implemented (2026-07-29)

## Backlog
- **P1**: Multi-device support; persistent queue across backend restarts; per-slider min/max safety caps set by owner.
- **P1**: Reconnect resilience for guest WS (auto-retry).
- **P2**: Usage history/audit log per code; scheduled/one-time codes; QR code for share links.
- **P2**: Funscript player relay (`stream:pos:time`) — firmware already supports it.

## Notes / Limitations
- Actual BLE motion control can ONLY be exercised with a real OSSM (firmware v3+) and a Web Bluetooth-capable browser (Chrome/Edge/Opera) physically near the device. Not testable in this cloud preview — protocol is implemented per firmware source.
