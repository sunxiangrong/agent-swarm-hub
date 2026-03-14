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
  export HTTP_PROXY="$ASH_PROXY_URL"
  export HTTPS_PROXY="$ASH_PROXY_URL"
  export all_proxy="$ASH_PROXY_URL"
  export ALL_PROXY="$ASH_PROXY_URL"
fi

echo "[agent-swarm-hub] starting Lark websocket listener in background"
if [[ "${CONDA_DEFAULT_ENV:-}" == "cli" ]]; then
  PYTHONPATH=src python -m agent_swarm_hub.cli lark-ws &
else
  PYTHONPATH=src conda run --live-stream -n cli python -m agent_swarm_hub.cli lark-ws &
fi
LARK_PID=$!

cleanup() {
  if kill -0 "$LARK_PID" >/dev/null 2>&1; then
    kill "$LARK_PID" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT INT TERM

echo "[agent-swarm-hub] running Telegram polling loop"
if [[ "${CONDA_DEFAULT_ENV:-}" == "cli" ]]; then
  PYTHONPATH=src python -m agent_swarm_hub.cli telegram-poll
else
  PYTHONPATH=src conda run --live-stream -n cli python -m agent_swarm_hub.cli telegram-poll
fi

echo "[agent-swarm-hub] Lark listener is still running under PID $LARK_PID"
echo "[agent-swarm-hub] press Ctrl+C to stop"
wait "$LARK_PID"
