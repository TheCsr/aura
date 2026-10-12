#!/usr/bin/env bash
# One command to test the LavinMQ pipeline: sets up .venv on first run, then
# opens the sensor UI in the browser. The agents' consumer starts inside the
# page, and LAVINMQ_URL is read from .env (default: local LavinMQ).
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/streamlit ]; then
  echo "First run: creating .venv and installing requirements (a few minutes)..."
  python3 -m venv .venv
  .venv/bin/pip install --quiet -r requirements.txt
fi

exec .venv/bin/streamlit run aura/sensors/ui.py --server.headless false
