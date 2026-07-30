# OSSM Bridge — Docker Deployment (macOS)

Runs the whole app (MongoDB + backend + frontend + HTTPS reverse proxy) as one
Docker Compose stack. No PM2, no Homebrew services, no manual `mongod`.

Bluetooth stays in Chrome on your Mac — Docker does **not** touch BLE.

## Prerequisites
- Docker Desktop installed and running.
- (For remote guests) Router port-forwards: **80 → Mac** and **443 → Mac**, and
  the DDNS name `tg30.ddns.net` pointing at your public IP.

## First run
```bash
cd /path/to/project
cp env.docker.example .env        # sets JWT_SECRET and DOMAIN
# edit .env -> set DOMAIN to your public domain (for the HTTPS certificate)
docker compose up -d --build
```

Then open **http://localhost** in Chrome on the Mac.
On the very first launch you'll be asked to **create the owner account**
(email + password) and set your **Local URL** and **Global/Public URL**:
- **Local URL** — where you open the app on this Mac (e.g. `http://localhost`). Used for the OBS overlay link.
- **Global/Public URL** — the public HTTPS address remote guests use (e.g. `https://tg30.ddns.net`). Guest share links are built from this.

You can change both later in the dashboard ("Base URLs" card).

> The `DOMAIN` in `.env` controls which domain Caddy fetches the TLS certificate
> for. The Global URL you set at first login controls the links shown in the app.
> Keep them consistent (same domain).

- Owner (local, BLE works):  http://localhost
- Remote guests (HTTPS):     https://tg30.ddns.net/c/CODE

> Web Bluetooth only works on `localhost` or `https://`, so the owner must use
> `http://localhost` (or the HTTPS domain) — never a plain `http://<ip>`.

## Everyday commands
```bash
docker compose up -d            # start (after reboot / Docker Desktop launch)
docker compose down             # stop (data is kept in named volumes)
docker compose logs -f backend  # backend logs
docker compose logs -f web      # Caddy / frontend logs
docker compose up -d --build    # rebuild after pulling code changes
```

## Survive reboots
- Docker Desktop → Settings → **Start Docker Desktop when you log in**.
- The services use `restart: unless-stopped`, so they auto-start with Docker.

## HTTPS certificate notes
Caddy fetches a Let's Encrypt cert for `tg30.ddns.net` automatically on first
request **once ports 80 and 443 reach the Mac**. If the cert isn't issued:
- Confirm the domain resolves to your current public IP.
- Confirm your router forwards 80 and 443 to this Mac.
- Check `docker compose logs -f web` for ACME errors.

Docker does **not** open router ports for you — port-forwarding (or a tunnel such
as Cloudflare Tunnel) is still required for public access.

## Data & reset
MongoDB data lives in the `mongo_data` volume. To wipe everything (including the
owner account) and start setup fresh:
```bash
docker compose down -v
```
