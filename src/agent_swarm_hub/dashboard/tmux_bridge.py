from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def load_tmux_project_panes() -> dict[str, list[dict[str, Any]]]:
    panes = _list_panes()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for pane in panes:
        workspace = str(pane.get("current_path") or "").strip()
        if not workspace:
            continue
        grouped.setdefault(workspace, []).append(pane)
    return grouped


def _list_panes() -> list[dict[str, Any]]:
    format_string = "\t".join(
        [
            "#{session_name}",
            "#{window_index}",
            "#{window_name}",
            "#{pane_id}",
            "#{pane_title}",
            "#{pane_active}",
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
        if len(parts) != 7:
            continue
        session_name, window_index, window_name, pane_id, pane_title, pane_active, current_path = parts
        panes.append(
            {
                "session_name": session_name,
                "window_index": window_index,
                "window_name": window_name,
                "pane_id": pane_id,
                "pane_title": pane_title,
                "active": pane_active == "1",
                "current_path": current_path,
                "preview": _capture_pane_preview(pane_id),
            }
        )
    return panes


def _capture_pane_preview(pane_id: str) -> str:
    if not pane_id:
        return ""
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-pt", pane_id, "-S", "-20"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return ""
    if result.returncode != 0:
        return ""
    lines = [" ".join(line.split()) for line in result.stdout.splitlines() if line.strip()]
    return " | ".join(lines[-3:])
