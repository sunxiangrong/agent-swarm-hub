from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .paths import repo_root


def ensure_orchestrator_pane(*, project_id: str, workspace_path: str, provider: str = "claude") -> dict[str, str]:
    workspace = str(workspace_path or "").strip()
    if not project_id or not workspace:
        return {"status": "skipped", "reason": "missing-project-or-path"}

    existing = _find_provider_pane(workspace_path=workspace, provider=provider)
    if existing:
        return {
            "status": "existing",
            "provider": provider,
            "pane_id": str(existing.get("pane_id") or ""),
            "session_name": str(existing.get("session_name") or ""),
            "window_index": str(existing.get("window_index") or ""),
            "title": str(existing.get("pane_title") or ""),
        }

    script = repo_root() / "scripts" / "start-chat.sh"
    command = f"cd {repo_root()} && ./scripts/start-chat.sh {provider} {project_id}"

    try:
        if os.getenv("TMUX"):
            window_name = f"ash-orch-{_slug(project_id)}"
            result = subprocess.run(
                ["tmux", "new-window", "-d", "-n", window_name, "-c", workspace, "/bin/bash", "-lc", command],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return {
                    "status": "launched",
                    "provider": provider,
                    "session_name": "",
                    "window_index": "",
                    "title": f"ash-chat | {project_id} | {provider}",
                }
            return {"status": "error", "reason": result.stderr.strip() or result.stdout.strip() or "tmux new-window failed"}

        session_name = f"ash-orch-{_slug(project_id)}"
        result = subprocess.run(
            ["tmux", "new-session", "-d", "-s", session_name, "-c", workspace, "/bin/bash", "-lc", command],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return {
                "status": "launched",
                "provider": provider,
                "session_name": session_name,
                "window_index": "0",
                "title": f"ash-chat | {project_id} | {provider}",
            }
        return {"status": "error", "reason": result.stderr.strip() or result.stdout.strip() or "tmux new-session failed"}
    except OSError as exc:
        return {"status": "error", "reason": str(exc)}


def _find_provider_pane(*, workspace_path: str, provider: str) -> dict[str, Any]:
    workspace_key = _resolve_workspace_key(workspace_path)
    provider_token = provider.strip().lower()
    for pane in _list_tmux_panes():
        current_path = _resolve_workspace_key(str(pane.get("current_path") or ""))
        if current_path != workspace_key:
            continue
        title = str(pane.get("pane_title") or "").lower()
        if provider_token in title:
            return pane
    return {}


def _list_tmux_panes() -> list[dict[str, Any]]:
    format_string = "\t".join(
        [
            "#{session_name}",
            "#{window_index}",
            "#{window_name}",
            "#{pane_id}",
            "#{pane_title}",
            "#{pane_current_path}",
        ]
    )
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", format_string],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return []
    if result.returncode != 0:
        return []
    panes: list[dict[str, Any]] = []
    for raw in result.stdout.splitlines():
        parts = raw.split("\t")
        if len(parts) != 6:
            continue
        session_name, window_index, window_name, pane_id, pane_title, current_path = parts
        panes.append(
            {
                "session_name": session_name,
                "window_index": window_index,
                "window_name": window_name,
                "pane_id": pane_id,
                "pane_title": pane_title,
                "current_path": current_path,
            }
        )
    return panes


def _resolve_workspace_key(path: str) -> str:
    raw = (path or "").strip()
    if not raw:
        return ""
    try:
        return str(Path(raw).expanduser().resolve())
    except OSError:
        return raw


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower()).strip("-")
    return cleaned or "default"
