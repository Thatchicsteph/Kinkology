#!/usr/bin/env bash
# OSSM Bridge — local macOS deployment (production build + PM2 process manager)
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
echo "▶ OSSM Bridge local deploy from $ROOT"

# ---------------------------------------------------------------------------
# 0. Prerequisites
# ---------------------------------------------------------------------------
command -v yarn >/dev/null || { echo "✗ yarn not found. brew install yarn"; exit 1; }
command -v python3 >/dev/null || { echo "✗ python3 not found. brew install python@3.11"; exit 1; }
command -v pm2 >/dev/null || { echo "✗ pm2 not found. Run: yarn global add pm2  (then re-run this script)"; exit 1; }

# ---------------------------------------------------------------------------
# 1. MongoDB reachable?
# ---------------------------------------------------------------------------
if ! nc -z localhost 27017 2>/dev/null; then
  echo "✗ MongoDB not reachable on localhost:27017."
  echo "  Start it with either:"
  echo "    brew services start mongodb-community@7.0"
  echo "    docker run -d --name mongo -p 27017:27017 -v mongo-data:/data/db mongo:7"
  exit 1
fi
echo "✓ MongoDB is up"

# ---------------------------------------------------------------------------
# 2. Backend: venv + deps + .env
# ---------------------------------------------------------------------------
cd "$ROOT/backend"
[ -d venv ] || python3 -m venv venv
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q -r requirements.txt
if [ ! -f .env ]; then
  echo "→ creating backend/.env"
  cat > .env <<EOF
MONGO_URL="mongodb://localhost:27017"
DB_NAME="ossm_bridge"
CORS_ORIGINS="*"
JWT_SECRET="$(openssl rand -hex 32)"
ADMIN_EMAIL="admin@ossm.local"
ADMIN_PASSWORD="ossm-admin-2026"
EOF
fi
echo "✓ backend ready"

# ---------------------------------------------------------------------------
# 3. Frontend: .env + install + production build
# ---------------------------------------------------------------------------
cd "$ROOT/frontend"
[ -f .env ] || echo 'REACT_APP_BACKEND_URL=http://localhost:8001' > .env
yarn install --silent
echo "→ building production frontend (this can take a couple of minutes)…"
yarn build
echo "✓ frontend built"

# ---------------------------------------------------------------------------
# 4. Launch with PM2
# ---------------------------------------------------------------------------
cd "$ROOT"
pm2 delete ossm-backend ossm-frontend >/dev/null 2>&1 || true
pm2 start "$ROOT/backend/venv/bin/uvicorn" --name ossm-backend --interpreter none \
  --cwd "$ROOT/backend" -- server:app --host 0.0.0.0 --port 8001
pm2 serve "$ROOT/frontend/build" 3000 --name ossm-frontend --spa
pm2 save

echo ""
echo "✅ OSSM Bridge is deployed and running."
echo "   Owner dashboard : http://localhost:3000/admin"
echo "   Live overlay    : http://localhost:3000/overlay"
echo "   Backend API     : http://localhost:8001/api/"
echo ""
echo "Useful: pm2 status | pm2 logs | pm2 restart all | pm2 stop all"
echo "To auto-start on reboot, run once:  pm2 startup   (then paste the command it prints)"
