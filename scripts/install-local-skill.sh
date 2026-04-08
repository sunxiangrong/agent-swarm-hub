#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL_NAME="${1:-}"

if [[ -z "$SKILL_NAME" ]]; then
  echo "Usage: scripts/install-local-skill.sh <skill-name>" >&2
  exit 2
fi

SOURCE_DIR="$ROOT_DIR/skills/$SKILL_NAME"
TARGET_DIR="${CODEX_HOME:-$HOME/.codex}/skills/$SKILL_NAME"

if [[ ! -f "$SOURCE_DIR/SKILL.md" ]]; then
  echo "Local skill source not found: $SOURCE_DIR/SKILL.md" >&2
  exit 2
fi

mkdir -p "$TARGET_DIR"
cp "$SOURCE_DIR/SKILL.md" "$TARGET_DIR/SKILL.md"

echo "Installed local skill:"
echo "  source: $SOURCE_DIR/SKILL.md"
echo "  target: $TARGET_DIR/SKILL.md"
