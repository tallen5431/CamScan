#!/usr/bin/env bash
# CamScan SessionStart hook — prepares a web session so tests and the app can run.
#   - Creates/reuses a .venv (same location Start.sh uses)
#   - Installs requirements.txt into it
#   - Puts the venv on PATH for the rest of the session
#   - Runs the headless smoke test so the session starts from a known-good state
set -euo pipefail

# Only run in Claude Code on the web (remote) sessions; local dev manages its own env.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
cd "$PROJECT_DIR"

VENV="$PROJECT_DIR/.venv"

# Create the venv once; reuse it on later runs (idempotent, cache-friendly).
if [ ! -x "$VENV/bin/python" ]; then
  echo "[session-start] creating virtualenv (.venv)"
  python3 -m venv "$VENV"
fi

echo "[session-start] installing dependencies from requirements.txt"
"$VENV/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
"$VENV/bin/python" -m pip install --quiet -r requirements.txt

# Make the venv the default interpreter for the rest of the session, so `python`,
# `pip`, and the smoke test all resolve to it without an explicit path.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export VIRTUAL_ENV=\"$VENV\"" >> "$CLAUDE_ENV_FILE"
  echo "export PATH=\"$VENV/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
fi

# Verify the app is runnable. Non-fatal: a red test surfaces its result but never
# blocks the session from starting.
echo "[session-start] running smoke test"
if ! "$VENV/bin/python" tests/smoke_test.py 2>&1 | grep -E '^\[(PASS|FAIL)\]|^SMOKE_RESULT'; then
  echo "[session-start] warning: smoke test produced no recognizable output"
fi

echo "[session-start] ready — start the server with:  .venv/bin/python app.py   (default PORT=8059)"
