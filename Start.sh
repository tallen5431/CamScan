#!/usr/bin/env bash
set -euo pipefail

# ============================================
# CamScan Starter (HTTPS-friendly via Caddy)
# - Creates/uses venv in .venv
# - Installs requirements.txt into venv only
# - Honors HOST / PORT env from Server Manager
# ============================================

# Resolve app directory (folder containing this script)
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

VENV_DIR="$APP_DIR/.venv"
PYTHON_EXE="$VENV_DIR/bin/python"

# Create virtual environment if needed
if [ ! -x "$PYTHON_EXE" ]; then
  echo "[SETUP] Creating virtualenv in $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

echo "[SETUP] Ensuring dependencies from requirements.txt..."
# Always use venv python + pip to avoid PEP 668 / system pip issues
"$PYTHON_EXE" -m pip install --upgrade pip setuptools wheel >/dev/null

if [ -f "$APP_DIR/requirements.txt" ]; then
  "$PYTHON_EXE" -m pip install -r "$APP_DIR/requirements.txt"
fi

# Network env (Server Manager will usually set these)
: "${PORT:=8059}"
: "${HOST:=0.0.0.0}"

export PORT HOST

echo "[RUN] Starting CamScan on http://$HOST:$PORT (LAN: http://$HOST:$PORT)"
exec "$PYTHON_EXE" "$APP_DIR/app.py"
