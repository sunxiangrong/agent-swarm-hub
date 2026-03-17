#!/usr/bin/env bash
set -euo pipefail

ASH_INVOKE_DIR="${PWD}"
export ASH_INVOKE_DIR
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

PROVIDER="${1:-${ASH_EXECUTOR:-codex}}"
PROJECT="${2:-}"

echo "[agent-swarm-hub] starting native entry"
echo "[agent-swarm-hub] provider=$PROVIDER"
if [[ -n "$PROJECT" ]]; then
  echo "[agent-swarm-hub] project=$PROJECT"
fi

if [[ "${CONDA_DEFAULT_ENV:-}" == "cli" ]]; then
  if [[ -n "$PROJECT" ]]; then
    PYTHONPATH=src python -m agent_swarm_hub.cli local-native --provider "$PROVIDER" --project "$PROJECT"
  else
    PYTHONPATH=src python -m agent_swarm_hub.cli local-native --provider "$PROVIDER"
  fi
else
  if [[ -n "$PROJECT" ]]; then
    PYTHONPATH=src conda run --live-stream -n cli python -m agent_swarm_hub.cli local-native --provider "$PROVIDER" --project "$PROJECT"
  else
    PYTHONPATH=src conda run --live-stream -n cli python -m agent_swarm_hub.cli local-native --provider "$PROVIDER"
  fi
fi
