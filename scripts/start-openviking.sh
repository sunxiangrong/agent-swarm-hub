#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_OUT="${OPENVIKING_CONFIG_OUT:-$ROOT_DIR/var/openviking/ov.conf}"

export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

conda run -n cli python -c '
from agent_swarm_hub.openviking_support import (
    build_openviking_config_from_env,
    read_openviking_config,
    validate_openviking_config,
    write_openviking_config,
)
import os
import sys

output = sys.argv[1]
has_openviking_env = any(
    os.environ.get(name)
    for name in (
        "OPENVIKING_ARK_API_KEY",
        "OPENVIKING_VLM_API_KEY",
        "OPENVIKING_EMBEDDING_API_KEY",
        "OPENVIKING_VLM_MODEL",
        "OPENVIKING_EMBEDDING_MODEL",
        "OPENVIKING_STORAGE_WORKSPACE",
    )
)
if has_openviking_env or not os.path.exists(output):
    config = build_openviking_config_from_env()
    validate_openviking_config(config)
    path = write_openviking_config(config, output)
else:
    validate_openviking_config(read_openviking_config(output))
    path = output
print(path)
' "$CONFIG_OUT"

if [[ "${1:-}" == "--write-only" ]]; then
  exit 0
fi

exec env OPENVIKING_CONFIG_FILE="$CONFIG_OUT" openviking-server "$@"
