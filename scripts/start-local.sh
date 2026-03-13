#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f ".env.local" ]]; then
  set -a
  # shellcheck disable=SC1091
  source ".env.local"
  set +a
fi

if [[ -n "${ASH_PROXY_URL:-}" ]]; then
  export http_proxy="$ASH_PROXY_URL"
  export https_proxy="$ASH_PROXY_URL"
fi

echo "[agent-swarm-hub] starting Lark websocket listener in background"
PYTHONPATH=src conda run -n cli python -m agent_swarm_hub.cli lark-ws &
LARK_PID=$!

cleanup() {
  if kill -0 "$LARK_PID" >/dev/null 2>&1; then
    kill "$LARK_PID" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT INT TERM

echo "[agent-swarm-hub] running one Telegram polling cycle"
PYTHONPATH=src conda run -n cli python -m agent_swarm_hub.cli telegram-poll --once

echo "[agent-swarm-hub] Lark listener is still running under PID $LARK_PID"
echo "[agent-swarm-hub] press Ctrl+C to stop"
wait "$LARK_PID"
