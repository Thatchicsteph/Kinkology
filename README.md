# Kinkology

Self-hosted web app to control an **OSSM** device over **browser Web Bluetooth**,
and share **time-limited remote control** with guests via access codes.

- **Owner** opens the app in Chrome/Edge/Opera on a machine physically near the
  device and clicks **Connect Device** — Bluetooth stays in the browser.
- **Guests** join with a shareable code/link, wait in a live queue, and get a
  countdown-limited turn. All guest commands are relayed and safety-clamped
  server-side.
- Extras: admin JWT login + TOTP 2FA + brute-force lockout, owner safety limits
  (min depth / max speed), and a public **/overlay** page with live telemetry
  graphs for OBS/streaming.

## Architecture

| Piece      | Tech                          | Notes |
|------------|-------------------------------|-------|
| Frontend   | React (CRA/craco)             | Web Bluetooth lives here (owner browser) |
| Backend    | FastAPI (`/api/*`) + WebSockets | Relays guest → host commands, enforces limits |
| Database   | MongoDB                       | Users, access codes, settings, telemetry, lockouts |
| Edge       | Caddy                         | HTTPS + single-origin reverse proxy |

> **Web Bluetooth requires `localhost` or HTTPS.** The owner must open the app
> from `http://localhost` (on the host machine) or a valid `https://` domain —
> a plain `http://<ip>` will not work.

---

## Deployment — Docker (recommended)

Runs the whole stack (MongoDB + backend + frontend + HTTPS proxy) with one
command. No PM2, Homebrew services, or manual `mongod`. Bluetooth stays in Chrome
on your machine — Docker does **not** touch BLE.

### Prerequisites
- **Docker Desktop** installed and running.
- (For remote guests) Router **port-forwards 80 → host** and **443 → host**, and
  a public domain (e.g. a DDNS name) pointing at your public IP.

### First run
```bash
cd /path/to/ossm-bridge
cp env.docker.example .env        # template for JWT_SECRET and DOMAIN
# edit .env → set DOMAIN, and set JWT_SECRET to a strong value (required):
#   openssl rand -hex 32
# edit .env → set DOMAIN to your public domain (used for the HTTPS certificate)
docker compose up -d --build
```

Open **http://localhost** in Chrome on the host machine. On first launch you'll:
1. **Create the owner account** (email + password — no admin is seeded).
2. Set your **Local URL** (e.g. `http://localhost`, used for the OBS overlay link)
   and **Global / Public URL** (e.g. `https://your-domain.com`, used to build
   guest share links). Both are editable later in the dashboard → *Base URLs*.

Access points once running:
- Owner (local, Bluetooth works): `http://localhost`
- Remote guests (HTTPS): `https://your-domain.com/c/<CODE>`
- Overlay (OBS browser source): `http://localhost/overlay` (append `?transparent=1`)

### Everyday commands
```bash
docker compose up -d            # start (after reboot / Docker Desktop launch)
docker compose down             # stop (data kept in named volumes)
docker compose up -d --build    # rebuild after pulling code changes
docker compose logs -f backend  # backend logs
docker compose logs -f web      # Caddy / frontend logs
```

### Survive reboots
- Docker Desktop → Settings → **Start Docker Desktop when you log in**.
- Services use `restart: unless-stopped`, so they auto-start with Docker.

### HTTPS certificate notes
Caddy fetches a Let's Encrypt cert for `DOMAIN` automatically on first request
**once ports 80 and 443 reach the host**. If it isn't issued:
- Confirm the domain resolves to your current public IP.
- Confirm the router forwards 80 and 443 to this machine.
- Check `docker compose logs -f web` for ACME errors.

Docker does **not** open router ports for you — port-forwarding (or a tunnel such
as Cloudflare Tunnel) is still required for public access. Keep `DOMAIN` in `.env`
consistent with the **Global URL** you set at first login.

### Reset everything
```bash
docker compose down -v          # wipes MongoDB (incl. owner account) → fresh setup
```

### Environment variables (`.env`)
| Var          | Purpose                                              |
|--------------|------------------------------------------------------|
| `JWT_SECRET` | Signs owner login tokens. Keep private. `openssl rand -hex 32` |
| `DOMAIN`     | Public domain Caddy requests the HTTPS certificate for |

---

## Local development (without Docker)

Requires Node 20 + Yarn, Python 3.11, and a running MongoDB.

```bash
# Backend
cd backend
pip install -r requirements.txt
# backend/.env needs: MONGO_URL, DB_NAME, JWT_SECRET
# (optional) ADMIN_EMAIL + ADMIN_PASSWORD to pre-seed an admin instead of first-run setup
uvicorn server:app --host 0.0.0.0 --port 8001 --reload

# Frontend (new terminal)
cd frontend
yarn install
# frontend/.env needs: REACT_APP_BACKEND_URL=http://localhost:8001
yarn start
```

Open `http://localhost:3000`. With no seeded admin, the first visit to
`/admin/login` prompts you to create the owner account.

---

## Toys — Lovense & other Bluetooth vibrating toys

The owner dashboard has a **Toys** panel alongside the Device Host bar. Rather
than hand-rolling a different Bluetooth protocol per toy brand (they vary a
lot), it connects to **[Intiface Central](https://intiface.com)** — a free,
open-source local app (built on the [Buttplug](https://buttplug.io) protocol)
that already knows how to talk to Lovense and dozens of other brands over
Bluetooth. This app effectively becomes the "toy" equivalent of the browser's
Web Bluetooth link to the OSSM.

**Toys work independently of the OSSM** — you can run a toy-only session with
no OSSM connected at all, or connect both together. The guest relay session
(the "Bridge") comes online whenever *either* the OSSM or Toys are connected.

**Setup:**
1. Install and open Intiface Central on the same machine as the owner's
   browser, then press **Start Server** (default `ws://127.0.0.1:12345`).
2. Put your toy(s) in pairing mode.
3. In the Kinkology owner dashboard, click **Connect Toys**. Found toys
   appear automatically — no OSSM connection required.
4. Choose a mode with the **LINKED TO SPEED / MANUAL** toggle:
   - **Linked to Speed** — every connected toy mirrors the app's `SPEED`
     control as vibration intensity. This is the "sync with OSSM" option:
     if an OSSM is also connected and running, toys ramp up and down with its
     stroke speed automatically, driven by whatever is setting SPEED (a
     guest, the owner's test console, or an auto program).
   - **Manual** — drive each toy's intensity independently with its own
     slider, regardless of what the OSSM is doing.

Toy control stays entirely in the owner's browser + local Intiface process,
same as the OSSM's BLE link — the backend never sees toy commands directly.

### MuSe / Love Spouse toys (cheap "Chinese" BLE vibrators)

These don't use standard Bluetooth GATT — the official app (and the toy
itself) only speak in one-way BLE *advertisement broadcasts*, not a normal
connect-and-write link. That means neither a browser (Web Bluetooth) nor
Intiface Central can talk to one directly — this isn't a limitation of this
app, it's how the hardware works.

The fix is a small **ESP32 gateway**, flashed with one of the existing
open-source firmwares for this exact protocol:
- [LS-Buttplug](https://github.com/Fi0nee/LS-Buttplug) (PlatformIO project,
  active development, English + Russian docs)
- [LVS-Gateway](https://github.com/IngeniousKink/LVS-Gateway)

Once flashed, the ESP32 sits in the middle: it shows up over Bluetooth to
Intiface Central as a normal Lovense toy, and on its other side it broadcasts
the Love Spouse advertisement packets the real toy listens for. From here on
it's identical to any other Intiface-connected toy — follow the **Toys**
setup steps above, no extra configuration in this app.

## Key routes

| Route            | Description |
|------------------|-------------|
| `/`              | Landing — guest code entry |
| `/admin/login`   | Owner setup / login (+ 2FA) |
| `/admin`         | Owner dashboard (device host, codes, limits, URLs, overlay link) |
| `/c/:code`       | Guest control console |
| `/overlay`       | Public live telemetry overlay (OBS) |
| `/api/*`         | Backend REST + WebSockets |

---

## Notes & limitations
- Actual BLE motion control requires a real OSSM (firmware v3+) and a
  Web Bluetooth–capable browser (Chrome/Edge/Opera) physically near the device.
  It cannot be exercised in a headless/cloud environment.
- For remote guests, Docker alone does not solve NAT/port-forwarding — HTTPS plus
  public routing (or a tunnel) is still required.
