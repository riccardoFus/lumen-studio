#!/usr/bin/env bash
# Avvia Lumen Studio su http://127.0.0.1:8790
set -euo pipefail
cd "$(dirname "$0")"

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8790}"
URL="http://${HOST}:${PORT}"

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="${PYTHON:-python3}"
fi

if [ "${LUMEN_NO_BROWSER:-0}" != "1" ] && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
  (
    sleep 1.6
    if command -v xdg-open >/dev/null 2>&1; then
      xdg-open "$URL" >/dev/null 2>&1 || true
    elif command -v sensible-browser >/dev/null 2>&1; then
      sensible-browser "$URL" >/dev/null 2>&1 || true
    fi
  ) &
fi

echo "Lumen Studio — ${URL}"
echo "Premi Ctrl+C per fermare il server."
exec "$PY" -m uvicorn app.main:app --host "$HOST" --port "$PORT" --log-level info
