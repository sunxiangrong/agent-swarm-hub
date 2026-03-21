from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .paths import repo_root


def ensure_orchestrator_pane(*, project_id: str, workspace_path: str, provider: str = "claude", launch_mode: str | None = None) -> dict[str, str]:
    workspace = str(workspace_path or "").strip()
    if not project_id or not workspace:
        return {"status": "skipped", "reason": "missing-project-or-path"}
    launch_mode = _resolve_launch_mode(launch_mode)

    existing = _find_provider_pane(workspace_path=workspace, provider=provider)
    if existing:
        return {
            "status": "existing",
            "launch_kind": "existing",
            "launch_mode": launch_mode,
            "provider": provider,
            "pane_id": str(existing.get("pane_id") or ""),
            "session_name": str(existing.get("session_name") or ""),
            "window_index": str(existing.get("window_index") or ""),
            "title": str(existing.get("pane_title") or ""),
        }

    script = repo_root() / "scripts" / "start-chat.sh"
    command = f"cd {repo_root()} && ASH_AUTO_ENTER_NATIVE=1 ./scripts/start-chat.sh {provider} {project_id}"

    try:
        if os.getenv("TMUX"):
            window_name = f"ash-{provider}-{_slug(project_id)}"
            result = subprocess.run(
                ["tmux", "new-window", "-d", "-n", window_name, "-c", workspace, "/bin/bash", "-lc", command],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                launched = _find_provider_pane(workspace_path=workspace, provider=provider)
                return {
                    "status": "launched",
                    "launch_kind": "window",
                    "launch_mode": launch_mode,
                    "provider": provider,
                    "session_name": str(launched.get("session_name") or ""),
                    "window_index": str(launched.get("window_index") or ""),
                    "pane_id": str(launched.get("pane_id") or ""),
                    "title": str(launched.get("pane_title") or f"ash-chat | {project_id} | {provider}"),
                }
            return {"status": "error", "reason": result.stderr.strip() or result.stdout.strip() or "tmux new-window failed"}

        session_name = f"ash-{provider}-{_slug(project_id)}"
        result = subprocess.run(
            ["tmux", "new-session", "-d", "-s", session_name, "-c", workspace, "/bin/bash", "-lc", command],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            launched = _find_provider_pane(workspace_path=workspace, provider=provider)
            return {
                "status": "launched",
                "launch_kind": "session",
                "launch_mode": launch_mode,
                "provider": provider,
                "session_name": str(launched.get("session_name") or session_name),
                "window_index": str(launched.get("window_index") or "0"),
                "pane_id": str(launched.get("pane_id") or ""),
                "title": str(launched.get("pane_title") or f"ash-chat | {project_id} | {provider}"),
            }
        return {"status": "error", "reason": result.stderr.strip() or result.stdout.strip() or "tmux new-session failed"}
    except OSError as exc:
        return {"status": "error", "reason": str(exc)}


def cleanup_tmux_launch(launch: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(launch, dict):
        return {"status": "skipped", "reason": "missing-launch"}
    if str(launch.get("status") or "") != "launched":
        return {"status": "skipped", "reason": "not-launched"}

    launch_kind = str(launch.get("launch_kind") or "").strip()
    session_name = str(launch.get("session_name") or "").strip()
    window_index = str(launch.get("window_index") or "").strip()
    pane_id = str(launch.get("pane_id") or "").strip()
    try:
        if launch_kind == "session" and session_name:
            result = subprocess.run(
                ["tmux", "kill-session", "-t", session_name],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return {"status": "cleaned", "target": session_name}
            return {"status": "error", "reason": result.stderr.strip() or result.stdout.strip() or "tmux kill-session failed"}
        if launch_kind == "window" and session_name and window_index:
            target = f"{session_name}:{window_index}"
            result = subprocess.run(
                ["tmux", "kill-window", "-t", target],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return {"status": "cleaned", "target": target}
            return {"status": "error", "reason": result.stderr.strip() or result.stdout.strip() or "tmux kill-window failed"}
        return {"status": "skipped", "reason": "missing-target", "target": pane_id}
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


def _resolve_launch_mode(launch_mode: str | None) -> str:
    raw = str(launch_mode or os.getenv("ASH_SWARM_TMUX_MODE") or "background").strip().lower()
    if raw not in {"background", "focus"}:
        return "background"
    return raw
