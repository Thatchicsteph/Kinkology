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

## Credentials
See `/app/memory/test_credentials.md`. Admin: `admin@ossm.local` / `ossm-admin-2026`.

## Backlog
- **P1**: Multi-device support; persistent queue across backend restarts; per-slider min/max safety caps set by owner.
- **P1**: Reconnect resilience for guest WS (auto-retry).
- **P2**: Usage history/audit log per code; scheduled/one-time codes; QR code for share links.
- **P2**: Funscript player relay (`stream:pos:time`) — firmware already supports it.

## Notes / Limitations
- Actual BLE motion control can ONLY be exercised with a real OSSM (firmware v3+) and a Web Bluetooth-capable browser (Chrome/Edge/Opera) physically near the device. Not testable in this cloud preview — protocol is implemented per firmware source.
