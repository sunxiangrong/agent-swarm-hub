from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .openviking_support import DEFAULT_IMPORT_TREE_ROOT
from .paths import repo_root
from .project_context import ProjectContextStore
from .session_store import SessionStore


ACTIVE_PHASES = {"planning", "executing", "verifying", "reviewing"}


@dataclass(frozen=True, slots=True)
class RuntimeCleanupAction:
    kind: str
    target: str
    reason: str
    applied: bool = False
    error: str = ""


def run_runtime_cleanup(
    *,
    apply: bool,
    session_store: SessionStore | None = None,
    project_store: ProjectContextStore | None = None,
    tmux_grace_minutes: int = 30,
    stale_workspace_days: int = 7,
    pane_log_days: int = 7,
    ccb_registry_days: int = 7,
    prune_openviking_imports: bool = False,
    openviking_import_days: int = 14,
    now_ts: float | None = None,
) -> dict[str, Any]:
    now = float(now_ts if now_ts is not None else time.time())
    session_store = session_store or SessionStore()
    project_store = project_store or ProjectContextStore()

    planned: list[RuntimeCleanupAction] = []
    errors: list[str] = []

    planned.extend(
        _plan_stale_tmux_sessions(
            session_store=session_store,
            grace_minutes=max(tmux_grace_minutes, 1),
            now_ts=now,
        )
    )
    planned.extend(
        _plan_stale_pane_logs(
            root=repo_root() / "var" / "panes" / "pane-logs",
            max_age_days=max(pane_log_days, 1),
            now_ts=now,
        )
    )
    planned.extend(
        _plan_stale_ccb_registry(
            registry_root=_ccb_registry_root(),
            max_age_days=max(ccb_registry_days, 1),
            now_ts=now,
        )
    )
    planned.extend(
        _plan_stale_workspace_records(
            session_store=session_store,
            max_age_days=max(stale_workspace_days, 1),
            now_ts=now,
        )
    )
    if prune_openviking_imports:
        planned.extend(
            _plan_stale_openviking_imports(
                project_store=project_store,
                import_root=DEFAULT_IMPORT_TREE_ROOT,
                max_age_days=max(openviking_import_days, 1),
                now_ts=now,
            )
        )

    applied_actions: list[RuntimeCleanupAction] = []
    if apply:
        for action in planned:
            result = _apply_action(action=action, session_store=session_store)
            applied_actions.append(result)
            if result.error:
                errors.append(f"{result.kind}: {result.target}: {result.error}")
    else:
        applied_actions = planned

    by_kind: dict[str, int] = {}
    applied_count = 0
    error_count = 0
    for item in applied_actions:
        by_kind[item.kind] = by_kind.get(item.kind, 0) + 1
        if item.applied:
            applied_count += 1
        if item.error:
            error_count += 1

    return {
        "mode": "apply" if apply else "dry-run",
        "total": len(applied_actions),
        "applied": applied_count,
        "errors": error_count,
        "by_kind": by_kind,
        "actions": [asdict(item) for item in applied_actions],
        "error_messages": errors,
    }


def _plan_stale_tmux_sessions(*, session_store: SessionStore, grace_minutes: int, now_ts: float) -> list[RuntimeCleanupAction]:
    protected = _protected_tmux_sessions(session_store=session_store, now_ts=now_ts)
    planned: list[RuntimeCleanupAction] = []
    for session in _list_tmux_sessions():
        name = str(session.get("name") or "")
        attached = int(session.get("attached") or 0)
        created_ts = int(session.get("created_ts") or 0)
        if not re.match(r"^ash-(codex|claude)-", name):
            continue
        if attached > 0:
            continue
        if name in protected:
            continue
        age_minutes = (now_ts - created_ts) / 60.0 if created_ts > 0 else grace_minutes + 1
        if age_minutes < grace_minutes:
            continue
        planned.append(
            RuntimeCleanupAction(
                kind="tmux_session_kill",
                target=name,
                reason=f"unattached for {int(age_minutes)}m (grace={grace_minutes}m)",
            )
        )
    return planned


def _plan_stale_pane_logs(*, root: Path, max_age_days: int, now_ts: float) -> list[RuntimeCleanupAction]:
    if not root.exists():
        return []
    planned: list[RuntimeCleanupAction] = []
    max_age_s = max_age_days * 24 * 60 * 60
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            age_s = now_ts - path.stat().st_mtime
        except OSError:
            continue
        if age_s < max_age_s:
            continue
        planned.append(
            RuntimeCleanupAction(
                kind="pane_log_delete",
                target=str(path),
                reason=f"older than {max_age_days}d",
            )
        )
    return planned


def _plan_stale_ccb_registry(*, registry_root: Path, max_age_days: int, now_ts: float) -> list[RuntimeCleanupAction]:
    if not registry_root.exists():
        return []
    planned: list[RuntimeCleanupAction] = []
    max_age_s = max_age_days * 24 * 60 * 60
    for path in sorted(registry_root.glob("ccb-session-*.json")):
        try:
            age_s = now_ts - path.stat().st_mtime
        except OSError:
            continue
        if age_s < max_age_s:
            continue
        planned.append(
            RuntimeCleanupAction(
                kind="ccb_registry_delete",
                target=str(path),
                reason=f"older than {max_age_days}d",
            )
        )
    return planned


def _plan_stale_workspace_records(*, session_store: SessionStore, max_age_days: int, now_ts: float) -> list[RuntimeCleanupAction]:
    max_age_s = max_age_days * 24 * 60 * 60
    states = _workspace_runtime_states(session_store=session_store)
    planned: list[RuntimeCleanupAction] = []
    for workspace in session_store.list_workspaces():
        workspace_id = workspace.workspace_id
        path = str(workspace.path or "").strip()
        state = states.get(workspace_id, {})
        updated_at = float(state.get("updated_ts") or 0.0)
        workspace_updated_at = _parse_iso_ts(str(workspace.updated_at or "").strip())
        baseline_updated_at = max(updated_at, workspace_updated_at)
        has_active_task = bool(state.get("has_active_task"))
        phase = str(state.get("phase") or "").strip().lower()
        try:
            exists = Path(path).expanduser().exists() if path else False
        except OSError:
            exists = False
        if exists:
            continue
        if has_active_task or phase in ACTIVE_PHASES:
            continue
        if baseline_updated_at > 0 and (now_ts - baseline_updated_at) < max_age_s:
            continue
        planned.append(
            RuntimeCleanupAction(
                kind="workspace_prune",
                target=workspace_id,
                reason=f"workspace path missing and inactive (>{max_age_days}d)",
            )
        )
    return planned


def _plan_stale_openviking_imports(
    *,
    project_store: ProjectContextStore,
    import_root: Path,
    max_age_days: int,
    now_ts: float,
) -> list[RuntimeCleanupAction]:
    if not import_root.exists():
        return []
    known_projects = {item.project_id for item in project_store.list_projects()}
    max_age_s = max_age_days * 24 * 60 * 60
    planned: list[RuntimeCleanupAction] = []
    for entry in sorted(import_root.iterdir()):
        if not entry.is_dir():
            continue
        project_id = entry.name
        if project_id in known_projects:
            continue
        try:
            age_s = now_ts - entry.stat().st_mtime
        except OSError:
            continue
        if age_s < max_age_s:
            continue
        planned.append(
            RuntimeCleanupAction(
                kind="ov_import_prune",
                target=str(entry),
                reason=f"orphan OV import (> {max_age_days}d)",
            )
        )
    return planned


def _apply_action(*, action: RuntimeCleanupAction, session_store: SessionStore) -> RuntimeCleanupAction:
    try:
        if action.kind == "tmux_session_kill":
            result = subprocess.run(
                ["tmux", "kill-session", "-t", action.target],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                return _with_action_result(action, error=result.stderr.strip() or result.stdout.strip() or "tmux kill-session failed")
            return _with_action_result(action, applied=True)
        if action.kind in {"pane_log_delete", "ccb_registry_delete"}:
            Path(action.target).unlink(missing_ok=True)
            return _with_action_result(action, applied=True)
        if action.kind == "workspace_prune":
            session_store.remove_workspace(action.target)
            return _with_action_result(action, applied=True)
        if action.kind == "ov_import_prune":
            shutil.rmtree(action.target, ignore_errors=False)
            return _with_action_result(action, applied=True)
        return _with_action_result(action, error="unsupported action")
    except Exception as exc:
        return _with_action_result(action, error=str(exc))


def _protected_tmux_sessions(*, session_store: SessionStore, now_ts: float) -> set[str]:
    protected: set[str] = set()
    recency_window_s = 3 * 60 * 60
    for workspace_id, state in _workspace_runtime_states(session_store=session_store).items():
        phase = str(state.get("phase") or "").strip().lower()
        has_active_task = bool(state.get("has_active_task"))
        updated_ts = float(state.get("updated_ts") or 0.0)
        if not has_active_task and phase not in ACTIVE_PHASES:
            continue
        if updated_ts > 0 and (now_ts - updated_ts) > recency_window_s:
            continue
        slug = _slug(workspace_id)
        protected.add(f"ash-codex-{slug}")
        protected.add(f"ash-claude-{slug}")
    return protected


def _workspace_runtime_states(*, session_store: SessionStore) -> dict[str, dict[str, Any]]:
    if not session_store.db_path.exists():
        return {}
    with session_store._connect() as conn:
        rows = conn.execute(
            """
            SELECT workspace_id, active_task_id, phase, updated_at
            FROM workspace_sessions
            ORDER BY updated_at DESC
            """
        ).fetchall()
    states: dict[str, dict[str, Any]] = {}
    for row in rows:
        workspace_id = str(row["workspace_id"] or "").strip()
        if not workspace_id or workspace_id in states:
            continue
        states[workspace_id] = {
            "has_active_task": bool(str(row["active_task_id"] or "").strip()),
            "phase": str(row["phase"] or "").strip().lower(),
            "updated_ts": _parse_iso_ts(str(row["updated_at"] or "").strip()),
        }
    return states


def _list_tmux_sessions() -> list[dict[str, Any]]:
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}\t#{session_attached}\t#{session_created}"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return []
    if result.returncode != 0:
        return []
    rows: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        try:
            attached = int(parts[1].strip() or "0")
        except ValueError:
            attached = 0
        try:
            created_ts = int(parts[2].strip() or "0")
        except ValueError:
            created_ts = 0
        rows.append(
            {
                "name": parts[0].strip(),
                "attached": attached,
                "created_ts": created_ts,
            }
        )
    return rows


def _parse_iso_ts(value: str) -> float:
    raw = (value or "").strip()
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", (value or "").strip().lower()).strip("-")
    return cleaned or "default"


def _ccb_registry_root() -> Path:
    raw = (os.getenv("ASH_CCB_RUN_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".ccb" / "run"


def _with_action_result(action: RuntimeCleanupAction, *, applied: bool = False, error: str = "") -> RuntimeCleanupAction:
    return RuntimeCleanupAction(
        kind=action.kind,
        target=action.target,
        reason=action.reason,
        applied=applied,
        error=error,
    )
