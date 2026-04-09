#!/bin/bash
# ─── TradeForge Server Deploy Script ───
# Run on Tencent Cloud: bash deploy.sh

set -e

echo "=== TradeForge Deploy ==="

# Auto-detect project directory
PROJECT_DIR=""
for d in /root/TradeStealth_Core /home/ubuntu/TradeStealth_Core /root/Agent-trade /home/ubuntu/Agent-trade; do
  if [ -d "$d/frontend_web" ]; then
    PROJECT_DIR="$d"
    break
  fi
done

# If not found, try find
if [ -z "$PROJECT_DIR" ]; then
  FOUND=$(find / -maxdepth 4 -name "next.config.ts" -path "*/frontend_web/*" 2>/dev/null | head -1)
  if [ -n "$FOUND" ]; then
    PROJECT_DIR=$(dirname $(dirname "$FOUND"))
  fi
fi

if [ -z "$PROJECT_DIR" ]; then
  echo "ERROR: Project not found. Cloning from GitHub..."
  cd /root
  git clone https://github.com/VivaLaVidad/Agent-trade.git TradeStealth_Core
  PROJECT_DIR="/root/TradeStealth_Core"
fi

echo "Project: $PROJECT_DIR"
cd "$PROJECT_DIR"

# Pull latest code
echo "=== Git Pull ==="
git pull origin main

# Build frontend
echo "=== Building Frontend ==="
cd frontend_web

# Install deps if needed
if [ ! -d "node_modules" ]; then
  echo "Installing dependencies..."
  npm install
fi

npm run build
echo "Frontend build OK"

# Restart or start PM2 process
echo "=== Restarting PM2 ==="
cd "$PROJECT_DIR/frontend_web"

# Try to find existing PM2 process for this app
PM2_NAME=$(pm2 jlist 2>/dev/null | grep -o '"name":"[^"]*"' | head -1 | cut -d'"' -f4)

if [ -n "$PM2_NAME" ]; then
  echo "Restarting PM2 process: $PM2_NAME"
  pm2 restart "$PM2_NAME"
else
  # Check if any pm2 process exists
  PM2_COUNT=$(pm2 jlist 2>/dev/null | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
  if [ "$PM2_COUNT" -gt "0" ]; then
    echo "Restarting all PM2 processes..."
    pm2 restart all
  else
    echo "Starting new PM2 process..."
    pm2 start npm --name "tradeforge-web" -- start
    pm2 save
  fi
fi

echo ""
echo "=== Deploy Complete ==="
echo "Visit: http://$(curl -s ifconfig.me 2>/dev/null || echo '211.159.225.45')"
pm2 list
