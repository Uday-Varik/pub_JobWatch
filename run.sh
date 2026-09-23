#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"
source .venv/bin/activate

# Prevent overlapping runs against the same jobwatch.db (a slow browser-lane
# run can exceed the cron interval; an overlapping run's delivery failure
# could reset this run's in-flight notification markers).
LOCK_DIR="/tmp/jobwatch.lock"
if mkdir "$LOCK_DIR" 2>/dev/null; then
    echo $$ > "$LOCK_DIR/pid"
    trap 'rm -rf "$LOCK_DIR"' EXIT
else
    LOCK_PID=$(cat "$LOCK_DIR/pid" 2>/dev/null || true)
    if [ -n "$LOCK_PID" ] && kill -0 "$LOCK_PID" 2>/dev/null; then
        echo "Another JobWatch run (PID $LOCK_PID) is in progress; skipping."
        exit 0
    fi
    # Stale lock from a hard-killed run — reclaim it.
    rm -rf "$LOCK_DIR"
    mkdir "$LOCK_DIR"
    echo $$ > "$LOCK_DIR/pid"
    trap 'rm -rf "$LOCK_DIR"' EXIT
fi

# Load email credentials from .env if it exists
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

LOG_DIR="./logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(date +%Y-%m-%d).log"

NEEDS_PLAYWRIGHT=1
case " $* " in
    *" --lane fast "*|*" --lane=fast "*)
        NEEDS_PLAYWRIGHT=0
        ;;
esac

if [ "$NEEDS_PLAYWRIGHT" -eq 1 ] && grep -q "ats: playwright" config.yaml; then
    if ! python -c "import playwright" >/dev/null 2>&1; then
        echo "Installing Playwright Python package..." | tee -a "$LOG_FILE"
        python -m pip install playwright >> "$LOG_FILE" 2>&1
    fi

    if ! python - <<'PY' >/dev/null 2>&1
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    browser.close()
PY
    then
        echo "Installing Chromium for Playwright..." | tee -a "$LOG_FILE"
        python -m playwright install chromium >> "$LOG_FILE" 2>&1
    fi
fi

echo "=== Run at $(date) ===" >> "$LOG_FILE"
python jobwatch.py run "$@" 2>&1 | tee -a "$LOG_FILE"
echo "" >> "$LOG_FILE"
