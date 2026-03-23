#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[agent-swarm-hub] running core regression suite"

conda run -n cli pytest \
  tests/test_cli.py -k "local_native or openviking or start_chat_script_routes_to_local_native"

conda run -n cli pytest \
  tests/test_runtime_cleanup.py \
  tests/test_swarm_launch.py \
  tests/test_dashboard.py \
  tests/test_openviking_support.py \
  tests/test_session_store_path.py

echo "[agent-swarm-hub] core regression suite passed"
