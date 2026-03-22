from __future__ import annotations

import sqlite3
from pathlib import Path
import json
import time
from typing import Any

from ..openviking_support import read_openviking_overview
from ..project_context import ProjectContextStore, project_ov_resource_uri
from ..session_store import SessionStore
from ..swarm_roles import resolve_swarm_roles
from .tmux_bridge import load_tmux_project_panes


def build_dashboard_snapshot(
    *,
    project_store: ProjectContextStore | None = None,
    session_store: SessionStore | None = None,
) -> dict[str, Any]:
    project_store = project_store or ProjectContextStore()
    session_store = session_store or SessionStore()
    project_payloads: list[dict[str, Any]] = []
    pinned_projects = project_store.list_pinned_projects()
    runtime_by_workspace = _load_runtime_sessions(session_store)
    swarm_by_workspace = _load_swarm_activity(session_store)
    tmux_by_workspace = load_tmux_project_panes()
    ccb_by_workspace = _load_ccb_provider_activity()
    for project in _iter_dashboard_projects(
        project_store,
        session_store,
        runtime_by_workspace=runtime_by_workspace,
        tmux_by_workspace=tmux_by_workspace,
        ccb_by_workspace=ccb_by_workspace,
    ):
        ov_resource_uri = project_ov_resource_uri(project["project_id"])
        ov_overview = _compact_text(read_openviking_overview(ov_resource_uri), 420)
        memory = project_store.get_project_memory(project["project_id"])
        brief = project_store.derive_session_brief(
            focus=memory.get("focus", "") or project_store._summary_field(project["summary"], "Current focus:"),
            recent_context=memory.get("recent_context", "") or project_store._summary_state(project["summary"]),
            memory=memory.get("memory", "") or project_store._summary_compact_text(project["summary"]),
            hints=memory.get("recent_hints", []),
        )
        current_sessions = project_store.get_current_project_sessions(project["project_id"])
        recorded_sessions = project_store.list_project_sessions(project["project_id"], include_archived=True)
        active_sessions = [row for row in recorded_sessions if row.get("status") == "active"]
        runtime = runtime_by_workspace.get(project["project_id"], [])
        swarm = swarm_by_workspace.get(project["project_id"], {})
        tmux_panes = tmux_by_workspace.get(_resolve_workspace_key(project["workspace_path"]), [])
        ccb_live = ccb_by_workspace.get(_resolve_workspace_key(project["workspace_path"]), [])
        live_session = runtime[0] if runtime else None
        live_phase = str((live_session or {}).get("phase") or "").strip()
        live_summary = _compact_text(str((live_session or {}).get("conversation_summary") or "").strip(), 160)
        current_session_line = ", ".join(f"{provider}: {session_id}" for provider, session_id in sorted(current_sessions.items()))
        roles = _workspace_swarm_roles(session_store, project["project_id"])
        driver_session_id = _driver_session_id(live_session, roles["orchestrator"])
        driver_tmux_pane = _select_driver_tmux_pane(tmux_panes, roles["orchestrator"])
        status = _project_status(
            has_runtime=bool(runtime),
            live_phase=live_phase,
            has_binding=bool(current_sessions),
            has_focus=bool(brief["focus"]),
        )
        project_summary_text = _project_summary_text(
            ov_overview=ov_overview,
            focus=brief["focus"],
            state=brief["current_state"],
            next_step=brief["next_step"],
            memory=brief["memory"],
            live_summary=live_summary,
        )
        memory_source_text = _memory_source_text(
            has_ov_overview=bool(ov_overview),
            has_stored_memory=bool(memory.get("focus") or memory.get("recent_context") or memory.get("memory") or memory.get("recent_hints")),
            has_project_summary=bool(project["summary"]),
            has_live_summary=bool(live_summary),
        )
        session_policy_text = _session_policy_text(
            current_sessions=current_sessions,
            recorded_sessions=recorded_sessions,
            active_sessions=active_sessions,
        )
        session_overview_text = _session_overview_text(
            current_session_line=current_session_line,
            active_sessions=active_sessions,
            recorded_sessions=recorded_sessions,
        )
        project_payloads.append(
            {
                "project_id": project["project_id"],
                "workspace_path": project["workspace_path"],
                "pinned": project["project_id"] in pinned_projects,
                "focus": brief["focus"],
                "current_state": brief["current_state"],
                "state": brief["current_state"],
                "next_step": brief["next_step"],
                "memory": brief["memory"],
                "ov_resource_uri": ov_resource_uri,
                "ov_overview": ov_overview,
                "project_summary_text": project_summary_text,
                "memory_source_text": memory_source_text,
                "current_sessions": current_session_line,
                "session_overview_text": session_overview_text,
                "session_policy_text": session_policy_text,
                "live_phase": live_phase,
                "live_summary": live_summary,
                "swarm_roles": roles,
                "swarm_roles_text": (
                    f"trigger={roles['trigger']} | orchestrator={roles['orchestrator']} | planner={roles['planner']} | "
                    f"executor={roles['executor']} | reviewer={roles['reviewer']}"
                ),
                "current_trigger": roles["trigger"],
                "current_driver": roles["driver"],
                "review_return_target": roles["orchestrator"],
                "driver_session_id": driver_session_id,
                "driver_tmux_pane_id": str(driver_tmux_pane.get("pane_id") or ""),
                "driver_tmux_title": str(driver_tmux_pane.get("pane_title") or ""),
                "driver_tmux_session_name": str(driver_tmux_pane.get("session_name") or ""),
                "driver_tmux_window_index": str(driver_tmux_pane.get("window_index") or ""),
                "swarm_active": bool(swarm.get("active")),
                "swarm_session_key": str(swarm.get("session_key") or ""),
                "swarm_task_id": str(swarm.get("task_id") or ""),
                "swarm_summary": str(swarm.get("summary") or ""),
                "swarm_planner_summary": str(swarm.get("planner_summary") or ""),
                "swarm_planned_subagents": list(swarm.get("planned_subagents") or []),
                "swarm_orchestrator_launch": dict(swarm.get("orchestrator_launch") or {}),
                "swarm_handoff_count": int(swarm.get("handoff_count") or 0),
                "swarm_agent_count": int(swarm.get("agent_count") or 0),
                "swarm_agents": list(swarm.get("agents") or []),
                "swarm_worker_agents": [item for item in list(swarm.get("agents") or []) if item.get("name") != "verification"],
                "swarm_verification_agents": [item for item in list(swarm.get("agents") or []) if item.get("name") == "verification"],
                "ccb_live_providers": ccb_live,
                "ccb_live_count": len(ccb_live),
                "tmux_panes": tmux_panes,
                "tmux_preview": tmux_panes[0]["preview"] if tmux_panes else "",
                "status": status,
                "active": bool(runtime) or bool(current_sessions) or bool(tmux_panes) or bool(ccb_live),
                "session_count_text": f"active {len(active_sessions)} / archived {max(len(recorded_sessions) - len(active_sessions), 0)}",
                "updated_at": project["updated_at"],
            }
        )
    project_payloads.sort(key=_project_sort_key)
    pinned_payloads = [item for item in project_payloads if item["pinned"]]
    active_projects = [item for item in project_payloads if item["active"]]
    watched_projects = pinned_payloads or active_projects
    return {
        "projects": project_payloads,
        "pinned_projects": pinned_payloads,
        "active_projects": active_projects,
        "watched_projects": watched_projects,
        "project_count": len(project_payloads),
        "pinned_project_count": len(pinned_payloads),
        "active_project_count": len(active_projects),
    }


def _iter_dashboard_projects(
    project_store: ProjectContextStore,
    session_store: SessionStore,
    *,
    runtime_by_workspace: dict[str, list[dict[str, Any]]],
    tmux_by_workspace: dict[str, list[dict[str, Any]]],
    ccb_by_workspace: dict[str, list[dict[str, Any]]],
) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = []
    known: set[str] = set()
    for project in project_store.list_projects():
        if not _should_include_dashboard_project(
            project_id=project.project_id,
            workspace_path=project.workspace_path,
            project_store=project_store,
            session_store=session_store,
            runtime_by_workspace=runtime_by_workspace,
            tmux_by_workspace=tmux_by_workspace,
            ccb_by_workspace=ccb_by_workspace,
        ):
            continue
        payload.append(
            {
                "project_id": project.project_id,
                "title": project.title,
                "workspace_path": project.workspace_path,
                "summary": project.summary,
                "updated_at": project_store._project_updated_at(project.project_id),
            }
        )
        known.add(project.project_id)
    for workspace in session_store.list_workspaces():
        if workspace.workspace_id in known:
            continue
        if not _should_include_dashboard_project(
            project_id=workspace.workspace_id,
            workspace_path=workspace.path,
            project_store=project_store,
            session_store=session_store,
            runtime_by_workspace=runtime_by_workspace,
            tmux_by_workspace=tmux_by_workspace,
            ccb_by_workspace=ccb_by_workspace,
        ):
            continue
        payload.append(
            {
                "project_id": workspace.workspace_id,
                "title": workspace.title,
                "workspace_path": workspace.path,
                "summary": "",
                "updated_at": workspace.updated_at,
            }
        )
    return payload


def _should_include_dashboard_project(
    *,
    project_id: str,
    workspace_path: str,
    project_store: ProjectContextStore,
    session_store: SessionStore,
    runtime_by_workspace: dict[str, list[dict[str, Any]]],
    tmux_by_workspace: dict[str, list[dict[str, Any]]],
    ccb_by_workspace: dict[str, list[dict[str, Any]]],
) -> bool:
    _ = (project_id, project_store, session_store, runtime_by_workspace, tmux_by_workspace, ccb_by_workspace)
    return _workspace_path_exists(workspace_path)


def _workspace_path_exists(path: str) -> bool:
    raw = str(path or "").strip()
    if not raw:
        return False
    try:
        return Path(raw).expanduser().exists()
    except OSError:
        return False


def _load_runtime_sessions(session_store: SessionStore) -> dict[str, list[dict[str, Any]]]:
    db_path = session_store.db_path
    if not db_path.exists():
        return {}
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT session_key, workspace_id, active_task_id, executor_session_id, claude_session_id, codex_session_id,
                   phase, conversation_summary, updated_at
            FROM workspace_sessions
            ORDER BY updated_at DESC
            """
        ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        workspace_id = str(row["workspace_id"] or "").strip()
        if not workspace_id:
            continue
        grouped.setdefault(workspace_id, []).append(
            {
                "session_key": row["session_key"],
                "active_task_id": row["active_task_id"] or "",
                "executor_session_id": row["executor_session_id"] or "",
                "claude_session_id": row["claude_session_id"] or "",
                "codex_session_id": row["codex_session_id"] or "",
                "phase": row["phase"] or "",
                "conversation_summary": row["conversation_summary"] or "",
                "updated_at": row["updated_at"] or "",
            }
        )
    return grouped


def _project_summary_text(*, ov_overview: str, focus: str, state: str, next_step: str, memory: str, live_summary: str) -> str:
    parts: list[str] = []
    if ov_overview:
        parts.append(f"OV: {ov_overview}")
    if focus:
        parts.append(f"Focus: {focus}")
    if state:
        parts.append(f"State: {state}")
    if next_step:
        parts.append(f"Next: {next_step}")
    if memory:
        parts.append(f"Cache: {memory}")
    if not parts and live_summary:
        parts.append(f"Live: {live_summary}")
    return " | ".join(parts[:4])


def _memory_source_text(*, has_ov_overview: bool, has_stored_memory: bool, has_project_summary: bool, has_live_summary: bool) -> str:
    if has_ov_overview:
        return "Primary source: OpenViking live project context under viking://resources/projects/<project-id>. Local cache and exported views follow this overview."
    if has_stored_memory:
        return "Primary source: stored project memory cache. Exported into local views such as projects.summary and PROJECT_MEMORY.md."
    if has_project_summary:
        return "Primary source: structured project summary. Sync memory to make it durable."
    if has_live_summary:
        return "Primary source: live runtime summary. Sync memory to promote it into durable project memory."
    return "No durable project memory yet."


def _session_policy_text(
    *,
    current_sessions: dict[str, str],
    recorded_sessions: list[dict[str, Any]],
    active_sessions: list[dict[str, Any]],
) -> str:
    active_count = len(active_sessions)
    archived_count = max(len(recorded_sessions) - active_count, 0)
    if current_sessions:
        return (
            f"Default resume uses current provider bindings ({len(current_sessions)} bound). "
            f"Recorded sessions: {active_count} active / {archived_count} archived. "
            "Switching a provider binding archives older same-provider sessions."
        )
    if recorded_sessions:
        return (
            f"No current binding. Recorded sessions: {active_count} active / {archived_count} archived. "
            "The next bind will choose the default resume session per provider."
        )
    return "No recorded provider sessions yet."


def _session_overview_text(
    *,
    current_session_line: str,
    active_sessions: list[dict[str, Any]],
    recorded_sessions: list[dict[str, Any]],
) -> str:
    active_count = len(active_sessions)
    archived_count = max(len(recorded_sessions) - active_count, 0)
    if current_session_line:
        return f"Current bindings: {current_session_line}. Recorded: {active_count} active / {archived_count} archived."
    return f"Recorded sessions: {active_count} active / {archived_count} archived."


def _load_swarm_activity(session_store: SessionStore) -> dict[str, dict[str, Any]]:
    db_path = session_store.db_path
    if not db_path.exists():
        return {}
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        workspace_rows = conn.execute(
            """
            SELECT session_key, workspace_id, active_task_id, phase, conversation_summary, swarm_state_json, updated_at
            FROM workspace_sessions
            ORDER BY updated_at DESC
            """
        ).fetchall()
        payload: dict[str, dict[str, Any]] = {}
        for row in workspace_rows:
            workspace_id = str(row["workspace_id"] or "").strip()
            session_key = str(row["session_key"] or "").strip()
            if not workspace_id or not session_key or workspace_id in payload:
                continue
            task_id = str(row["active_task_id"] or "").strip()
            swarm_state_json = str(row["swarm_state_json"] or "").strip()
            if not task_id and not swarm_state_json:
                continue
            handoffs = conn.execute(
                """
                SELECT handoff_type, source_agent, target_agent, content_json, created_at
                FROM task_handoffs
                WHERE session_key = ? AND workspace_id = ? AND task_id = ?
                ORDER BY id DESC
                LIMIT 20
                """,
                (session_key, workspace_id, task_id),
            ).fetchall() if task_id else []
            planner_summary, planned_subagents = _extract_execution_plan(handoffs)
            payload[workspace_id] = {
                "active": True,
                "session_key": session_key,
                "task_id": task_id,
                "summary": _compact_text(str(row["conversation_summary"] or "").strip(), 180),
                "planner_summary": planner_summary,
                "planned_subagents": planned_subagents,
                "orchestrator_launch": _extract_orchestrator_launch(str(row["conversation_summary"] or "").strip()),
                "handoff_count": len(handoffs),
                "agent_count": 0,
                "agents": _summarize_swarm_agents(handoffs),
            }
            payload[workspace_id]["agent_count"] = len(payload[workspace_id]["agents"])
        return payload


def _load_ccb_provider_activity() -> dict[str, list[dict[str, Any]]]:
    registry_dir = Path.home() / ".ccb" / "run"
    if not registry_dir.exists():
        return {}
    now = int(time.time())
    grouped: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(registry_dir.glob("ccb-session-*.json")):
        record = _read_registry_record(path)
        if not record:
            continue
        updated_at = _registry_updated_at(record, path)
        if updated_at <= 0 or (now - updated_at) > 7 * 24 * 60 * 60:
            continue
        workspace_key = _resolve_workspace_key(str(record.get("work_dir") or ""))
        if not workspace_key:
            continue
        providers = record.get("providers")
        if not isinstance(providers, dict):
            continue
        for provider_name, provider_entry in providers.items():
            if not isinstance(provider_name, str) or not isinstance(provider_entry, dict):
                continue
            pane_id = str(provider_entry.get("pane_id") or "").strip()
            marker = str(provider_entry.get("pane_title_marker") or "").strip()
            if not pane_id and not marker:
                continue
            session_id = ""
            for key in (
                f"{provider_name}_session_id",
                "codex_session_id",
                "claude_session_id",
                "gemini_session_id",
                "opencode_session_id",
            ):
                value = str(provider_entry.get(key) or "").strip()
                if value:
                    session_id = value
                    break
            grouped.setdefault(workspace_key, []).append(
                {
                    "provider": provider_name.strip().lower(),
                    "pane_id": pane_id,
                    "pane_title_marker": marker,
                    "session_id": session_id,
                    "updated_at": updated_at,
                    "ccb_session_id": str(record.get("ccb_session_id") or "").strip(),
                }
            )
    for workspace_key, rows in grouped.items():
        rows.sort(key=lambda item: (item.get("provider") or "", -(int(item.get("updated_at") or 0))))
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for row in rows:
            dedupe_key = (
                str(row.get("provider") or ""),
                str(row.get("pane_id") or ""),
                str(row.get("session_id") or ""),
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            deduped.append(row)
        grouped[workspace_key] = deduped
    return grouped


def _read_registry_record(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _registry_updated_at(record: dict[str, Any], path: Path) -> int:
    value = record.get("updated_at")
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    try:
        return int(path.stat().st_mtime)
    except OSError:
        return 0


def _summarize_swarm_agents(handoffs: list[sqlite3.Row]) -> list[dict[str, Any]]:
    agents: dict[str, dict[str, Any]] = {}
    for row in reversed(handoffs):
        handoff_type = str(row["handoff_type"] or "").strip()
        source_agent = str(row["source_agent"] or "").strip()
        target_agent = str(row["target_agent"] or "").strip()
        content = _parse_content_json(str(row["content_json"] or ""))
        if handoff_type == "subagent_packet" and target_agent:
            item = agents.setdefault(
                target_agent,
                {
                    "name": target_agent,
                    "status": "pending",
                    "backend": "",
                    "strategy": "",
                    "summary": "",
                    "launch_status": "",
                    "launch_pane_id": "",
                    "cleanup_status": "",
                    "cleanup_target": "",
                },
            )
            item["summary"] = _compact_text(str(content.get("task") or content.get("instructions") or item["summary"]), 120)
            worker_launch = content.get("worker_launch")
            if isinstance(worker_launch, dict):
                item["launch_status"] = str(worker_launch.get("status") or item.get("launch_status") or "")
                item["launch_pane_id"] = str(worker_launch.get("pane_id") or item.get("launch_pane_id") or "")
        elif handoff_type == "subagent_result" and source_agent:
            backend = str(content.get("backend") or "").strip()
            strategy = str(content.get("strategy") or "").strip()
            output = _compact_text(str(content.get("output") or "").strip(), 120)
            item = agents.setdefault(
                source_agent,
                {
                    "name": source_agent,
                    "status": "completed",
                    "backend": backend,
                    "strategy": strategy,
                    "summary": output,
                    "launch_status": "",
                    "launch_pane_id": "",
                    "cleanup_status": "",
                    "cleanup_target": "",
                },
            )
            item["backend"] = backend or item.get("backend", "")
            item["strategy"] = strategy or item.get("strategy", "")
            item["summary"] = output or item.get("summary", "")
            item["status"] = "failed" if backend == "error" or output.lower().startswith("execution error") else "completed"
            worker_launch = content.get("worker_launch")
            if isinstance(worker_launch, dict):
                item["launch_status"] = str(worker_launch.get("status") or item.get("launch_status") or "")
                item["launch_pane_id"] = str(worker_launch.get("pane_id") or item.get("launch_pane_id") or "")
            worker_cleanup = content.get("worker_cleanup")
            if isinstance(worker_cleanup, dict):
                item["cleanup_status"] = str(worker_cleanup.get("status") or item.get("cleanup_status") or "")
                item["cleanup_target"] = str(worker_cleanup.get("target") or item.get("cleanup_target") or "")
        elif handoff_type == "verification_packet":
            item = agents.setdefault(
                "verification",
                {
                    "name": "verification",
                    "status": "running",
                    "backend": target_agent or "codex",
                    "strategy": "",
                    "summary": "Verification requested.",
                    "launch_status": "",
                    "launch_pane_id": "",
                    "cleanup_status": "",
                    "cleanup_target": "",
                },
            )
            item["backend"] = target_agent or item.get("backend", "")
        elif handoff_type == "verification_result":
            backend = str(content.get("backend") or "").strip()
            output = _compact_text(str(content.get("output") or "").strip(), 120)
            item = agents.setdefault(
                "verification",
                {
                    "name": "verification",
                    "status": "completed",
                    "backend": backend or source_agent,
                    "strategy": "",
                    "summary": output,
                    "launch_status": "",
                    "launch_pane_id": "",
                    "cleanup_status": "",
                    "cleanup_target": "",
                },
            )
            item["backend"] = backend or source_agent or item.get("backend", "")
            item["summary"] = output or item.get("summary", "")
            item["status"] = "failed" if backend == "error" or output.lower().startswith("execution error") else "completed"
    return list(agents.values())


def _extract_execution_plan(handoffs: list[sqlite3.Row]) -> tuple[str, list[str]]:
    for row in handoffs:
        if str(row["handoff_type"] or "").strip() != "execution_plan":
            continue
        content = _parse_content_json(str(row["content_json"] or ""))
        planner_summary = _compact_text(str(content.get("planner_output") or "").strip(), 240)
        raw_subagents = content.get("suggested_subagents") or []
        planned_subagents = [str(item).strip() for item in raw_subagents if str(item).strip()]
        return planner_summary, planned_subagents
    return "", []


def _extract_orchestrator_launch(summary: str) -> dict[str, Any]:
    for line in summary.splitlines():
        text = line.strip()
        if not text.startswith("Orchestrator: "):
            continue
        rest = text.removeprefix("Orchestrator: ").strip()
        if rest.endswith(")") and "(" in rest:
            provider, _, status = rest.rpartition("(")
            return {
                "provider": provider.strip(),
                "status": status.rstrip(")").strip(),
            }
        return {"provider": rest, "status": ""}
    return {}


def _parse_content_json(value: str) -> dict[str, Any]:
    if not value:
        return {}
    try:
        item = json.loads(value)
    except Exception:
        return {}
    return item if isinstance(item, dict) else {}


def _project_status(*, has_runtime: bool, live_phase: str, has_binding: bool, has_focus: bool) -> str:
    if has_runtime and live_phase:
        return live_phase
    if has_runtime:
        return "active"
    if has_binding:
        return "bound"
    if has_focus:
        return "tracked"
    return "idle"


def _workspace_swarm_roles(session_store: SessionStore, workspace_id: str) -> dict[str, str]:
    workspace = session_store.get_workspace(workspace_id)
    roles = resolve_swarm_roles(workspace.backend if workspace and workspace.backend else None)
    return {
        "driver": roles.orchestrator,
        "trigger": roles.trigger,
        "orchestrator": roles.orchestrator,
        "planner": roles.planner,
        "executor": roles.executor,
        "reviewer": roles.reviewer,
    }


def _driver_session_id(live_session: dict[str, Any] | None, driver: str) -> str:
    if not live_session:
        return ""
    if driver == "codex":
        return str(live_session.get("codex_session_id") or live_session.get("executor_session_id") or "")
    if driver == "claude":
        return str(live_session.get("claude_session_id") or live_session.get("executor_session_id") or "")
    return str(live_session.get("executor_session_id") or "")


def _select_driver_tmux_pane(tmux_panes: list[dict[str, Any]], driver: str) -> dict[str, Any]:
    driver_token = (driver or "").strip().lower()
    if not driver_token:
        return tmux_panes[0] if tmux_panes else {}
    for pane in tmux_panes:
        title = str(pane.get("pane_title") or "").lower()
        session_name = str(pane.get("session_name") or "").lower()
        if driver_token in title or driver_token in session_name:
            return pane
    return tmux_panes[0] if tmux_panes else {}


def _project_sort_key(item: dict[str, Any]) -> tuple[int, str, str]:
    priority = 0 if item.get("pinned") else 1 if item.get("active") else 2
    updated_at = str(item.get("updated_at") or "")
    return (priority, updated_at == "", updated_at or item["project_id"])


def _compact_text(value: str, limit: int) -> str:
    text = " ".join(value.split()).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _resolve_workspace_key(path: str) -> str:
    raw = (path or "").strip()
    if not raw:
        return ""
    try:
        return str(Path(raw).expanduser().resolve())
    except OSError:
        return raw
