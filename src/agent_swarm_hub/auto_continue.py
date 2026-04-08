from __future__ import annotations
"""Single-step automatic project continuation helpers.

This module owns the first automation rung above runtime-health hardening:
read the current project state, decide whether one safe auto-continue step is
possible, execute exactly one step, then write the result back into project
memory/session state.
"""

import json
from typing import Any, Callable

from .executor import build_executor_for_config
from .project_context import ProjectContextStore
from .remote import RemoteMessage, RemotePlatform
from .session_store import SessionStore

_BLOCKED_RUNTIME_HEALTH = {
    "quarantined",
    "unhealthy",
    "orphan-running",
    "missing-binding-process",
}


def parse_auto_continue_request(argument: str) -> dict[str, str | bool]:
    provider = ""
    explain = False
    for token in (argument or "").strip().split():
        lowered = token.lower()
        if lowered in {"--explain", "explain"}:
            explain = True
        elif lowered in {"claude", "codex"}:
            provider = lowered
    return {"provider": provider, "explain": explain}


def _auto_message(*, project_id: str, text: str) -> RemoteMessage:
    return RemoteMessage(
        platform=RemotePlatform.LOCAL,
        chat_id=f"auto-runtime:{project_id}",
        user_id="auto-runtime",
        text=text,
    )


def _build_auto_continue_prompt(
    *,
    project_id: str,
    current_phase: str,
    current_state: str,
    next_step: str,
    last_verified_result: str,
    runtime_health_summary: str,
) -> str:
    lines = [
        "Auto-continue this project with exactly one meaningful incremental step.",
        f"Project: {project_id}",
    ]
    if current_phase:
        lines.append(f"Current phase: {current_phase}")
    if current_state:
        lines.append(f"Current state: {current_state}")
    if last_verified_result:
        lines.append(f"Last verified result: {last_verified_result}")
    if runtime_health_summary:
        lines.append(f"Runtime health: {runtime_health_summary}")
    lines.append(f"Next step: {next_step}")
    lines.append(
        "Execute only this next best step once. Stop after one concrete increment, "
        "report what changed, and mention any blocker instead of continuing further."
    )
    return "\n".join(lines)


def build_auto_continue_plan(
    project_id: str,
    *,
    context_store: ProjectContextStore | None = None,
) -> dict[str, str | int]:
    store = context_store or ProjectContextStore()
    project = store.get_project(project_id)
    if project is None:
        return {"code": 2, "message": f"Unknown project: {project_id}"}
    workspace_path = (project.workspace_path or "").strip()
    if not workspace_path:
        return {"code": 2, "message": f"Project `{project_id}` has no enterable path."}

    snapshot = store.build_memory_snapshot(workspace_path)
    projection = store.build_daily_projection(project_id) or {}
    runtime_status = str(snapshot.get("runtime_health_status") or "").strip()
    runtime_summary = str(snapshot.get("runtime_health_summary") or "").strip()
    next_step = str(projection.get("next_step") or "").strip()
    current_phase = str(projection.get("current_phase") or snapshot.get("current_phase") or "").strip()
    current_state = str(projection.get("current_state") or "").strip()
    last_verified_result = str(projection.get("last_verified_result") or snapshot.get("last_verified_result") or "").strip()

    if runtime_status in _BLOCKED_RUNTIME_HEALTH:
        blocked_text = (
            f"Auto-continue blocked by runtime health for `{project_id}`: "
            f"{runtime_status or 'unknown'}"
        )
        if runtime_summary:
            blocked_text = f"{blocked_text}\n{runtime_summary}"
        return {
            "code": 1,
            "message": blocked_text,
            "runtime_status": runtime_status,
            "runtime_summary": runtime_summary,
        }
    if not next_step:
        return {
            "code": 0,
            "message": f"No auto-continue candidate is available for `{project_id}`.",
            "runtime_status": runtime_status,
            "runtime_summary": runtime_summary,
        }
    return {
        "code": 0,
        "project_id": project_id,
        "workspace_path": workspace_path,
        "runtime_status": runtime_status,
        "runtime_summary": runtime_summary,
        "current_phase": current_phase,
        "current_state": current_state,
        "next_step": next_step,
        "last_verified_result": last_verified_result,
        "prompt": _build_auto_continue_prompt(
            project_id=project_id,
            current_phase=current_phase,
            current_state=current_state,
            next_step=next_step,
            last_verified_result=last_verified_result,
            runtime_health_summary=runtime_summary,
        ),
    }


def render_auto_continue_plan(plan: dict[str, str | int]) -> str:
    if int(plan.get("code", 0)) != 0:
        return str(plan.get("message") or "")
    prompt = str(plan.get("prompt") or "").strip()
    if not prompt:
        return str(plan.get("message") or "No auto-continue candidate is available.")
    lines = [
        f"Auto-step project: {plan.get('project_id', '')}",
    ]
    current_phase = str(plan.get("current_phase") or "").strip()
    current_state = str(plan.get("current_state") or "").strip()
    runtime_summary = str(plan.get("runtime_summary") or "").strip()
    last_verified_result = str(plan.get("last_verified_result") or "").strip()
    next_step = str(plan.get("next_step") or "").strip()
    if current_phase:
        lines.append(f"Phase: {current_phase}")
    if current_state:
        lines.append(f"Current state: {current_state}")
    if runtime_summary:
        lines.append(f"Runtime health: {runtime_summary}")
    if last_verified_result:
        lines.append(f"Last verified result: {last_verified_result}")
    if next_step:
        lines.append(f"Next step: {next_step}")
    lines.append("Explain only: no execution performed.")
    return "\n".join(lines)


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            payload = json.loads(text[start : end + 1])
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}
    return {}


def _build_completion_check_prompt(
    *,
    project_id: str,
    current_phase: str,
    current_state: str,
    next_step: str,
    last_verified_result: str,
    runtime_health_summary: str,
) -> str:
    lines = [
        "Evaluate whether this project task can stop after the latest auto-continue step.",
        f"Project: {project_id}",
    ]
    if current_phase:
        lines.append(f"Current phase: {current_phase}")
    if current_state:
        lines.append(f"Current state: {current_state}")
    if last_verified_result:
        lines.append(f"Last verified result: {last_verified_result}")
    if runtime_health_summary:
        lines.append(f"Runtime health: {runtime_health_summary}")
    if next_step:
        lines.append(f"Current next step: {next_step}")
    lines.append(
        'Reply with JSON only using keys: "status", "reason", "next_step", "blocker", "needs_confirmation". '
        'Allowed status values: "active", "completed", "blocked", "needs_confirmation".'
    )
    return "\n".join(lines)


def evaluate_auto_continue_completion(
    project_id: str,
    *,
    provider: str | None,
    context_store: ProjectContextStore | None = None,
) -> dict[str, Any]:
    store = context_store or ProjectContextStore()
    project = store.get_project(project_id)
    if project is None:
        return {"status": "active", "reason": f"Unknown project: {project_id}", "next_step": "", "blocker": "", "needs_confirmation": False}
    workspace_path = (project.workspace_path or "").strip()
    if not workspace_path:
        return {"status": "active", "reason": f"Project `{project_id}` has no enterable path.", "next_step": "", "blocker": "", "needs_confirmation": False}

    snapshot = store.build_memory_snapshot(workspace_path)
    projection = store.build_daily_projection(project_id) or {}
    prompt = _build_completion_check_prompt(
        project_id=project_id,
        current_phase=str(projection.get("current_phase") or snapshot.get("current_phase") or "").strip(),
        current_state=str(projection.get("current_state") or "").strip(),
        next_step=str(projection.get("next_step") or "").strip(),
        last_verified_result=str(projection.get("last_verified_result") or snapshot.get("last_verified_result") or "").strip(),
        runtime_health_summary=str(snapshot.get("runtime_health_summary") or "").strip(),
    )
    resolved_provider = (provider or "codex").strip().lower() or "codex"
    executor = build_executor_for_config(
        mode=resolved_provider,
        transport="auto",
        work_dir=workspace_path,
    )
    result = executor.run(prompt)
    payload = _parse_json_object(result.output)
    status = str(payload.get("status") or "active").strip().lower()
    if status not in {"active", "completed", "blocked", "needs_confirmation"}:
        status = "active"
    reason = str(payload.get("reason") or "").strip()
    next_step = str(payload.get("next_step") or "").strip()
    blocker = str(payload.get("blocker") or "").strip()
    needs_confirmation = bool(payload.get("needs_confirmation") or status == "needs_confirmation")
    return {
        "status": status,
        "reason": reason,
        "next_step": next_step,
        "blocker": blocker,
        "needs_confirmation": needs_confirmation,
        "provider": resolved_provider,
        "backend": result.backend,
    }


def _sync_auto_continue_memory(
    *,
    adapter: Any,
    project_id: str,
    session_key: str,
    sync_project_memory_artifacts_cb: Callable[[ProjectContextStore, str], None],
) -> None:
    adapter._sync_project_memory(session_key=session_key, workspace_id=project_id)  # type: ignore[attr-defined]
    memory_key = adapter._memory_key(session_key, project_id)  # type: ignore[attr-defined]
    recent_rows = adapter.store.list_recent_messages(memory_key, limit=6)
    recent_messages = [f"{row['role']}: {row['text']}" for row in recent_rows if (row["text"] or "").strip()]
    workspace_session = adapter.store.get_workspace_session(session_key, project_id)
    live_summary = workspace_session.conversation_summary if workspace_session and workspace_session.conversation_summary else ""
    adapter.project_context_store.consolidate_project_memory(
        project_id,
        live_summary=live_summary,
        recent_messages=recent_messages,
    )
    sync_project_memory_artifacts_cb(adapter.project_context_store, project_id)


def project_sessions_auto_continue(
    project_id: str,
    *,
    provider: str | None,
    explain: bool,
    sync_project_memory_artifacts_cb: Callable[[ProjectContextStore, str], None],
) -> int:
    context_store = ProjectContextStore()
    plan = build_auto_continue_plan(project_id, context_store=context_store)
    resolved_provider = (provider or "codex").strip().lower() or "codex"
    if int(plan["code"]) != 0 or not str(plan.get("prompt") or "").strip():
        context_store.record_auto_continue_state(
            project_id,
            resolved_provider,
            status="blocked",
            summary=str(plan.get("message") or "Auto-continue is currently blocked."),
            details={
                "mode": "execute" if not explain else "explain",
                "runtime_status": str(plan.get("runtime_status") or ""),
            },
        )
        print(str(plan.get("message") or ""))
        return int(plan["code"])
    if explain:
        context_store.record_auto_continue_state(
            project_id,
            resolved_provider,
            status="planned",
            summary=f"Auto-continue plan ready via {resolved_provider}: {str(plan.get('next_step') or '').strip()}",
            details={
                "mode": "explain",
                "phase": str(plan.get("current_phase") or ""),
                "next_step": str(plan.get("next_step") or ""),
                "runtime_status": str(plan.get("runtime_status") or ""),
            },
        )
        print(f"Requested provider: {resolved_provider}")
        print(render_auto_continue_plan(plan))
        return 0

    session_store = SessionStore()
    workspace = session_store.get_workspace(project_id)
    project = context_store.get_project(project_id)
    resolved_provider = (provider or (workspace.backend if workspace else "") or "codex").strip().lower() or "codex"
    session_store.upsert_workspace(
        workspace_id=project_id,
        title=(project.title if project else "") or project_id,
        path=str(plan["workspace_path"]),
        backend=resolved_provider,
        transport="auto",
    )
    from .adapter import CCConnectAdapter

    adapter = CCConnectAdapter(
        executor=build_executor_for_config(
            mode=resolved_provider,
            transport="auto",
            work_dir=str(plan["workspace_path"]),
        ),
        store=session_store,
    )
    use_message = _auto_message(project_id=project_id, text=f"/use {project_id}")
    adapter.handle_message(use_message)
    run_message = _auto_message(project_id=project_id, text=str(plan["prompt"]))
    response = adapter.handle_message(run_message)
    context_store.record_auto_continue_state(
        project_id,
        resolved_provider,
        status="executed",
        summary=f"Auto-continue executed one step via {resolved_provider}: {str(plan.get('next_step') or '').strip()}",
        details={
            "mode": "execute",
            "phase": str(plan.get("current_phase") or ""),
            "next_step": str(plan.get("next_step") or ""),
            "task_id": str(response.task_id or ""),
        },
    )
    _sync_auto_continue_memory(
        adapter=adapter,
        project_id=project_id,
        session_key=run_message.session_key,
        sync_project_memory_artifacts_cb=sync_project_memory_artifacts_cb,
    )
    print(f"Auto-continue project: {project_id}")
    print(f"Provider: {resolved_provider}")
    if str(plan.get("current_phase") or "").strip():
        print(f"Phase: {plan['current_phase']}")
    print(f"Next step: {plan['next_step']}")
    print(response.text)
    return 0
