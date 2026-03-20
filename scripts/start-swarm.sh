#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
mkdir -p var/db var/log var/panes

export ASH_SESSION_DB="${ASH_SESSION_DB:-var/db/agent-swarm-hub.sqlite3}"

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
PANE_PROJECT="${PROJECT:-no-project}"

set_tmux_pane_title() {
  if [[ -z "${TMUX:-}" ]]; then
    return 0
  fi
  if ! command -v tmux >/dev/null 2>&1; then
    return 0
  fi
  local title="$1"
  tmux select-pane -T "$title" >/dev/null 2>&1 || true
}

set_tmux_pane_title "ash-swarm | ${PANE_PROJECT} | ${PROVIDER}"

echo "[agent-swarm-hub] starting swarm shell"
echo "[agent-swarm-hub] provider=$PROVIDER"
if [[ -n "$PROJECT" ]]; then
  echo "[agent-swarm-hub] project=$PROJECT"
fi

if [[ "${CONDA_DEFAULT_ENV:-}" == "cli" ]]; then
  if [[ -n "$PROJECT" ]]; then
    PYTHONPATH=src python -m agent_swarm_hub.cli local-chat --provider "$PROVIDER" --project "$PROJECT"
  else
    PYTHONPATH=src python -m agent_swarm_hub.cli local-chat --provider "$PROVIDER"
  fi
else
  if [[ -n "$PROJECT" ]]; then
    PYTHONPATH=src conda run --live-stream -n cli python -m agent_swarm_hub.cli local-chat --provider "$PROVIDER" --project "$PROJECT"
  else
    PYTHONPATH=src conda run --live-stream -n cli python -m agent_swarm_hub.cli local-chat --provider "$PROVIDER"
  fi
fi
