from __future__ import annotations
"""Native provider launch workflow for Claude/Codex project sessions.

This module owns the heavy local-native path: session discovery, workspace
resolution, project-context injection, duplicate Codex guardrails, and post-run
memory/session reconciliation. cli.py keeps wrappers so shell entrypoints and
tests can keep patching stable names.
"""

import json
import os
import sqlite3
import subprocess
import sys
import time
from hashlib import sha1
from pathlib import Path

from .paths import project_session_db_path, provider_command
from .project_context import ProjectContextStore, project_ov_resource_uri
from .session_store import SessionStore, WorkspaceRecord
from .openviking_support import read_openviking_overview


def resolve_workspace_record(*, store: SessionStore, workspace_id: str | None, provider: str) -> WorkspaceRecord | None:
    if not workspace_id:
        return None
    workspace = store.get_workspace(workspace_id)
    project = ProjectContextStore().get_project(workspace_id)
    if project is None:
        return workspace

    resolved = WorkspaceRecord(
        workspace_id=workspace_id,
        title=project.title or (workspace.title if workspace else workspace_id),
        path=project.workspace_path or (workspace.path if workspace else ""),
        backend=workspace.backend if workspace and workspace.backend else provider,
        transport=workspace.transport if workspace and workspace.transport else "direct",
        created_at=workspace.created_at if workspace else "",
        updated_at=workspace.updated_at if workspace else "",
    )
    store.upsert_workspace(
        workspace_id=resolved.workspace_id,
        title=resolved.title,
        path=resolved.path,
        backend=resolved.backend,
        transport=resolved.transport,
    )
    return store.get_workspace(workspace_id) or resolved


def latest_provider_session(
    *,
    project_id: str,
    provider: str,
    workspace_path: str | None,
    context_store: ProjectContextStore | None = None,
) -> str | None:
    store = context_store or ProjectContextStore()
    bound_session_id = store.get_provider_binding(project_id, provider)
    if bound_session_id and provider_session_exists(provider=provider, session_id=bound_session_id):
        return bound_session_id

    db_path = project_session_db_path()
    if not db_path.exists():
        return None

    def fetch_latest(conn: sqlite3.Connection, resolved_project_id: str) -> str | None:
        provider_columns = {row["name"] for row in conn.execute("PRAGMA table_info(provider_sessions)").fetchall()}
        status_order = "CASE WHEN status = 'active' THEN 0 ELSE 1 END, " if "status" in provider_columns else ""
        rows = conn.execute(
            f"""
            SELECT raw_session_id
            FROM provider_sessions
            WHERE project_id = ? AND provider = ?
            ORDER BY {status_order} last_used_at DESC, raw_session_id DESC
            """,
            (resolved_project_id, provider),
        ).fetchall()
        for row in rows:
            session_id = row["raw_session_id"] if row is not None else None
            if session_id and provider_session_matches_workspace(
                provider=provider,
                session_id=session_id,
                workspace_path=workspace_path,
            ):
                store.set_provider_binding(resolved_project_id, provider, session_id)
                return session_id
        return None

    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            session_id = fetch_latest(conn, project_id)
            if session_id:
                return session_id
            if not workspace_path:
                return None
            project_row = conn.execute(
                """
                SELECT project_id
                FROM projects
                WHERE workspace_path = ?
                ORDER BY updated_at DESC, project_id ASC
                LIMIT 1
                """,
                (str(Path(workspace_path).expanduser().resolve()),),
            ).fetchone()
            if project_row is None:
                return None
            return fetch_latest(conn, project_row["project_id"])
    except sqlite3.Error:
        return None


def provider_session_matches_workspace(*, provider: str, session_id: str, workspace_path: str | None) -> bool:
    if provider != "codex" or not (workspace_path or "").strip():
        return True
    session_file = find_codex_session_file(session_id)
    if session_file is None:
        return False
    session_cwd = read_codex_session_cwd(session_file)
    if not session_cwd:
        return False
    try:
        workspace = Path(workspace_path).expanduser().resolve()
        session_path = Path(session_cwd).expanduser().resolve()
    except OSError:
        return False
    return session_path == workspace or workspace in session_path.parents


def provider_session_exists(*, provider: str, session_id: str) -> bool:
    if not session_id:
        return False
    if provider == "codex":
        return find_codex_session_file(session_id) is not None
    return True


def find_codex_session_file(session_id: str) -> Path | None:
    root = Path.home() / ".codex" / "sessions"
    if not root.exists():
        return None
    matches = list(root.glob(f"**/*{session_id}*.jsonl"))
    return matches[0] if matches else None


def find_running_codex_session(*, session_id: str | None, work_dir: str | None) -> dict[str, str] | None:
    if not session_id and not work_dir:
        return None
    try:
        result = subprocess.run(
            ["ps", "-ax", "-o", "pid=,command="],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    stdout = str(getattr(result, "stdout", "") or "")
    if not stdout:
        return None
    session_marker = f"resume {session_id}" if session_id else ""
    work_dir_marker = f"-C {work_dir}".strip() if work_dir else ""
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line or "codex-aarch64-apple-darwin" not in line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        pid, command = parts
        if session_marker and session_marker in command:
            return {"pid": pid.strip(), "command": command}
        if work_dir_marker and work_dir_marker in command:
            return {"pid": pid.strip(), "command": command}
    return None


def read_codex_session_cwd(session_file: Path) -> str:
    try:
        with session_file.open("r", encoding="utf-8", errors="ignore") as handle:
            first_line = handle.readline().strip()
    except OSError:
        return ""
    if not first_line:
        return ""
    try:
        entry = json.loads(first_line)
    except Exception:
        return ""
    if not isinstance(entry, dict):
        return ""
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    cwd = payload.get("cwd")
    return cwd.strip() if isinstance(cwd, str) else ""


def project_provider_sessions(*, project_id: str, workspace_path: str | None, context_store: ProjectContextStore | None = None) -> dict[str, str]:
    store = context_store or ProjectContextStore()
    sessions: dict[str, str] = {}
    for provider in ("claude", "codex"):
        session_id = latest_provider_session(
            project_id=project_id,
            provider=provider,
            workspace_path=workspace_path,
            context_store=store,
        )
        if session_id:
            sessions[provider] = session_id
    return sessions


def project_summary_field(summary: str, prefix: str) -> str:
    return ProjectContextStore._summary_field(summary, prefix)


def build_project_summary_prompt(
    *,
    workspace_id: str,
    work_dir: str,
    summary: str,
    snapshot: dict[str, str],
    driver_provider: str,
    read_openviking_overview_cb=read_openviking_overview,
) -> str:
    focus = project_summary_field(summary, "Current focus:") or snapshot.get("focus") or ""
    recent_context = ProjectContextStore._summary_state(summary) or snapshot.get("recent_context") or ""
    provider_driver = driver_provider or "claude"
    brief = ProjectContextStore.derive_session_brief(
        focus=focus,
        recent_context=recent_context,
        memory=snapshot.get("memory") or ProjectContextStore._summary_compact_text(summary),
        hints=snapshot.get("recent_hints") or [],
    )
    lines = [
        "Project summary for this session:",
        f"- Project: {workspace_id}",
        f"- Path: {work_dir}",
    ]
    if brief["focus"]:
        lines.append(f"- Current Focus: {brief['focus']}")
    if brief["current_state"]:
        lines.append(f"- Current State: {brief['current_state']}")
    if brief["next_step"]:
        lines.append(f"- Next Step: {brief['next_step']}")
    if brief["memory"]:
        lines.append(f"- Cache Summary: {brief['memory']}")
    if snapshot.get("global_memory"):
        lines.append(f"- Shared Global Memory: {snapshot['global_memory']}")
    ov_overview = ProjectContextStore._compact(
        read_openviking_overview_cb(project_ov_resource_uri(workspace_id)).replace("\n", " ").strip(),
        240,
    )
    if ov_overview:
        lines.append(f"- OpenViking Overview: {ov_overview}")
    lines.extend(
        [
            "- Swarm Mode: complex tasks may automatically enter coordinated multi-agent execution",
            f"- Current Trigger: {provider_driver}",
            "- Swarm Orchestrator: claude (launched in tmux when coordination starts)",
            "- Coordination Roles: orchestrator=claude, planner=claude, executor=codex, reviewer=claude",
            "- Return Target: claude",
        ]
    )
    lines.append(f"- OpenViking Project Context: {project_ov_resource_uri(workspace_id)}")
    lines.append(f"- Local Memory View: {work_dir}/PROJECT_MEMORY.md")
    lines.append(f"- Local Rules View: {work_dir}/PROJECT_SKILL.md")
    lines.append("Use the OpenViking project context as the project-scoped source when available; use the local files as exported startup views.")
    return "\n".join(lines)


def print_project_entry_view(
    *,
    provider: str,
    workspace_id: str,
    work_dir: str,
    project_summary: str,
    snapshot: dict[str, str],
    resume_session_id: str | None,
) -> None:
    print(f"[agent-swarm-hub] entering native {provider} CLI")
    print(f"[agent-swarm-hub] project={workspace_id}")
    print(f"[agent-swarm-hub] path={work_dir}")
    focus = project_summary_field(project_summary, "Current focus:") or snapshot.get("focus") or ""
    recent_context = ProjectContextStore._summary_state(project_summary) or snapshot.get("recent_context") or ""
    compact_summary = ProjectContextStore._summary_compact_text(project_summary) or snapshot.get("memory") or ""
    if focus:
        print(f"[agent-swarm-hub] focus={focus}")
    if recent_context:
        print(f"[agent-swarm-hub] current_state={recent_context}")
    elif compact_summary:
        print(f"[agent-swarm-hub] summary={compact_summary}")
    if resume_session_id:
        print(f"[agent-swarm-hub] current_{provider}_session={resume_session_id}")
    else:
        print(f"[agent-swarm-hub] current_{provider}_session=none")


def confirm_project_entry(*, provider: str) -> None:
    if str(os.getenv("ASH_AUTO_ENTER_NATIVE") or "").strip().lower() in {"1", "true", "yes", "on"}:
        print(f"[agent-swarm-hub] auto-enter native {provider} CLI")
        return
    print(f"Press Enter to enter native {provider} CLI...")
    input()


def provider_launch_argv(
    *,
    provider: str,
    command: str,
    session_id: str | None,
    work_dir: str,
    bootstrap_prompt: str = "",
) -> list[str]:
    if session_id:
        if provider == "codex":
            argv = [command, "--no-alt-screen", "-C", work_dir, "resume", session_id]
            if bootstrap_prompt:
                argv.append(bootstrap_prompt)
            return argv
        if provider == "claude":
            return [command, "--resume", session_id]
        return [command]
    if provider == "codex":
        argv = [command, "--no-alt-screen", "-C", work_dir]
        if bootstrap_prompt:
            argv.append(bootstrap_prompt)
        return argv
    return [command]


def _scripts_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "scripts"


def _prepend_path(env: dict[str, str], entry: str) -> None:
    current = env.get("PATH", "")
    if not current:
        env["PATH"] = entry
        return
    parts = current.split(os.pathsep)
    if entry in parts:
        env["PATH"] = current
        return
    env["PATH"] = os.pathsep.join([entry, *parts])


def clear_project_runtime_env(env: dict[str, str]) -> None:
    for key in (
        "ASH_ACTIVE_WORKSPACE",
        "ASH_PROJECT_PATH",
        "ASH_PROJECT_PROVIDER",
        "ASH_PROJECT_SESSION_MODE",
        "ASH_PROJECT_SESSION_ID",
        "ASH_PROJECT_IDENTITY_TEXT",
        "ASH_PROJECT_MEMORY_PROJECT_ID",
        "ASH_PROJECT_MEMORY_WORKSPACE",
        "ASH_PROJECT_MEMORY_PROFILE",
        "ASH_PROJECT_MEMORY_FOCUS",
        "ASH_PROJECT_MEMORY_RECENT_CONTEXT",
        "ASH_PROJECT_MEMORY_SUMMARY",
        "ASH_PROJECT_MEMORY_HINTS",
        "ASH_PROJECT_MEMORY_OVERVIEW",
        "ASH_GLOBAL_MEMORY_SUMMARY",
        "ASH_GLOBAL_MEMORY_HINTS",
        "ASH_PROVIDER_SESSION_ID",
        "ASH_CLAUDE_SESSION_ID",
        "ASH_CODEX_SESSION_ID",
        "CCB_WORK_DIR",
        "CCB_RUN_DIR",
    ):
        env.pop(key, None)


def inject_project_identity_env(
    env: dict[str, str],
    *,
    workspace_id: str | None,
    work_dir: str,
    provider: str,
    provider_session_id: str | None,
    session_mode: str,
) -> None:
    env["ASH_PROJECT_PROVIDER"] = provider
    env["ASH_PROJECT_SESSION_MODE"] = session_mode
    env["ASH_PROJECT_SESSION_ID"] = provider_session_id or ""
    env["ASH_PROJECT_IDENTITY_TEXT"] = (
        f"project={workspace_id or 'temporary'} | "
        f"path={work_dir} | "
        f"provider={provider} | "
        f"session_mode={session_mode}"
        + (f" | session_id={provider_session_id}" if provider_session_id else "")
    )
    scripts_dir = _scripts_dir()
    _prepend_path(env, str(scripts_dir))
    env["ASH_PROJECT_WHERE_COMMAND"] = "ash-where"


def inject_project_memory_env(
    env: dict[str, str],
    *,
    workspace_path: str | None,
    context_store: ProjectContextStore | None = None,
    snapshot: dict[str, str] | None = None,
    read_openviking_overview_cb=read_openviking_overview,
) -> bool:
    snapshot = snapshot or (context_store or ProjectContextStore()).build_memory_snapshot(workspace_path)
    if not snapshot.get("project_id"):
        return False
    env["ASH_PROJECT_MEMORY_PROJECT_ID"] = snapshot["project_id"]
    env["ASH_PROJECT_MEMORY_WORKSPACE"] = snapshot["workspace"]
    env["ASH_PROJECT_MEMORY_PROFILE"] = snapshot["profile"]
    env["ASH_PROJECT_MEMORY_FOCUS"] = snapshot["focus"]
    env["ASH_PROJECT_MEMORY_CURRENT_STATE"] = snapshot["recent_context"]
    env["ASH_PROJECT_MEMORY_RECENT_CONTEXT"] = snapshot["recent_context"]
    env["ASH_PROJECT_MEMORY_SUMMARY"] = snapshot["memory"]
    env["ASH_PROJECT_MEMORY_HINTS"] = " || ".join(snapshot["recent_hints"])
    env["ASH_PROJECT_MEMORY_OVERVIEW"] = read_openviking_overview_cb(
        project_ov_resource_uri(snapshot["project_id"])
    )
    env["ASH_GLOBAL_MEMORY_SUMMARY"] = str(snapshot.get("global_memory") or "")
    env["ASH_GLOBAL_MEMORY_HINTS"] = " || ".join(snapshot.get("global_hints", []) or [])
    return True


def workspace_path_matches(workspace_path: str | None, candidate_cwd: str | None) -> bool:
    if not (workspace_path or "").strip() or not (candidate_cwd or "").strip():
        return False
    try:
        workspace = Path(workspace_path).expanduser().resolve()
        candidate = Path(candidate_cwd).expanduser().resolve()
    except OSError:
        return False
    return candidate == workspace or workspace in candidate.parents


def extract_codex_history(session_id: str) -> list[str]:
    history_path = Path.home() / ".codex" / "history.jsonl"
    if not history_path.exists() or not session_id:
        return []
    messages: list[str] = []
    try:
        with history_path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except Exception:
                    continue
                if str(payload.get("session_id") or "") != session_id:
                    continue
                text = str(payload.get("text") or "").strip()
                if text:
                    messages.append(f"user: {text}")
    except OSError:
        return []
    return messages[-6:]


def extract_claude_text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and item.get("text"):
                parts.append(str(item["text"]).strip())
        return "\n".join(part for part in parts if part).strip()
    return ""


def collect_codex_workspace_sessions(workspace_path: str | None) -> dict[str, dict[str, object]]:
    root = Path.home() / ".codex" / "sessions"
    if not root.exists():
        return {}
    sessions: dict[str, dict[str, object]] = {}
    for path in root.glob("**/*.jsonl"):
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                first_line = handle.readline().strip()
        except OSError:
            continue
        if not first_line:
            continue
        try:
            entry = json.loads(first_line)
        except Exception:
            continue
        if not isinstance(entry, dict):
            continue
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        session_id = str(payload.get("id") or "").strip()
        cwd = str(payload.get("cwd") or "").strip()
        if not session_id or not workspace_path_matches(workspace_path, cwd):
            continue
        stat = path.stat()
        sessions[session_id] = {
            "session_id": session_id,
            "cwd": cwd,
            "source_path": str(path),
            "sort_key": int(stat.st_mtime_ns),
            "last_used_at": str(int(stat.st_mtime)),
            "messages": extract_codex_history(session_id),
        }
    return sessions


def collect_claude_workspace_sessions(workspace_path: str | None) -> dict[str, dict[str, object]]:
    root = Path.home() / ".claude" / "projects"
    if not root.exists():
        return {}
    sessions: dict[str, dict[str, object]] = {}
    for path in root.glob("*/*.jsonl"):
        session_id = path.stem
        cwd = ""
        last_used_at = ""
        messages: list[str] = []
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    raw = line.strip()
                    if not raw:
                        continue
                    try:
                        payload = json.loads(raw)
                    except Exception:
                        continue
                    cwd = str(payload.get("cwd") or cwd).strip()
                    last_used_at = str(payload.get("timestamp") or last_used_at).strip()
                    session_id = str(payload.get("sessionId") or session_id).strip()
                    role = payload.get("type")
                    if role not in {"user", "assistant"}:
                        continue
                    message = payload.get("message") or {}
                    text = extract_claude_text(message.get("content"))
                    if text:
                        messages.append(f"{role}: {text}")
        except OSError:
            continue
        if not session_id or not workspace_path_matches(workspace_path, cwd):
            continue
        stat = path.stat()
        sessions[session_id] = {
            "session_id": session_id,
            "cwd": cwd,
            "source_path": str(path),
            "sort_key": int(stat.st_mtime_ns),
            "last_used_at": last_used_at or str(int(stat.st_mtime)),
            "messages": messages[-6:],
        }
    return sessions


def collect_workspace_provider_sessions(provider: str, workspace_path: str | None) -> dict[str, dict[str, object]]:
    if provider == "codex":
        return collect_codex_workspace_sessions(workspace_path)
    if provider == "claude":
        return collect_claude_workspace_sessions(workspace_path)
    return {}


def select_postrun_session(
    *,
    provider: str,
    workspace_path: str | None,
    before: dict[str, dict[str, object]],
    preferred_session_id: str | None,
) -> dict[str, object] | None:
    after = collect_workspace_provider_sessions(provider, workspace_path)
    if not after:
        return None
    if preferred_session_id and preferred_session_id in after:
        preferred = after[preferred_session_id]
        previous = before.get(preferred_session_id)
        if previous is None or int(preferred["sort_key"]) >= int(previous.get("sort_key", 0)):
            return preferred
    changed = [
        meta
        for session_id, meta in after.items()
        if session_id not in before or int(meta["sort_key"]) > int(before[session_id].get("sort_key", 0))
    ]
    if changed:
        return max(changed, key=lambda item: int(item.get("sort_key", 0)))
    return max(after.values(), key=lambda item: int(item.get("sort_key", 0)))


def record_provider_binding_and_memory(
    *,
    context_store: ProjectContextStore,
    project_id: str,
    provider: str,
    workspace_path: str,
    session_meta: dict[str, object] | None,
    fallback_snapshot: dict[str, str],
    consolidate_project_memory_artifacts_cb,
) -> None:
    if not session_meta or not project_id:
        return
    session_id = str(session_meta.get("session_id") or "").strip()
    if not session_id:
        return
    db_path = project_session_db_path()
    messages = [str(item).strip() for item in session_meta.get("messages", []) if str(item).strip()]
    notes = messages[-1][:120] if messages else ""
    title = ""
    for item in reversed(messages):
        if item.startswith("user:"):
            title = item.removeprefix("user:").strip()[:80]
            break
    if not title:
        title = notes
    source_path = str(session_meta.get("source_path") or "")
    cwd = str(session_meta.get("cwd") or workspace_path)
    last_used_at = str(session_meta.get("last_used_at") or "")
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, notes, source_path, cwd, last_used_at)
                VALUES (?, ?, ?, 'active', ?, ?, ?, ?)
                ON CONFLICT(provider, raw_session_id) DO UPDATE SET
                    project_id=excluded.project_id,
                    status='active',
                    notes=excluded.notes,
                    source_path=excluded.source_path,
                    cwd=excluded.cwd,
                    last_used_at=excluded.last_used_at
                """,
                (provider, session_id, project_id, notes, source_path, cwd, last_used_at),
            )
            conn.commit()
    except sqlite3.Error:
        pass
    context_store.upsert_project_session(
        project_id,
        provider,
        session_id,
        status="active",
        title=title,
        summary=" | ".join(messages[-2:]) if messages else notes,
        cwd=cwd,
        source_path=source_path,
        last_used_at=last_used_at,
    )
    context_store.set_provider_binding(project_id, provider, session_id)
    fallback_focus = fallback_snapshot.get("focus") or ""
    fallback_recent_context = fallback_snapshot.get("recent_context") or ""
    fallback_memory = fallback_snapshot.get("memory") or ""
    fallback_hints = fallback_snapshot.get("recent_hints", [])
    extracted = extract_project_memory_from_messages(messages, fallback_snapshot=fallback_snapshot)
    focus = extracted["focus"]
    recent_context = extracted["recent_context"]
    memory = extracted["memory"]
    hints = extracted["recent_hints"]
    if extracted["fallback_only"]:
        focus = fallback_focus
        recent_context = fallback_recent_context
        memory = fallback_memory
        hints = [str(item).strip() for item in fallback_hints if str(item).strip()]
    context_store.upsert_project_memory(
        project_id,
        focus=focus,
        recent_context=recent_context,
        memory=memory,
        recent_hints=hints or fallback_snapshot.get("recent_hints", []),
    )
    consolidate_project_memory_artifacts_cb(
        context_store,
        project_id,
        live_summary=" | ".join(messages[-3:]) if messages else "",
        recent_messages=messages,
    )


def sync_native_workspace_runtime(
    *,
    session_store: SessionStore,
    context_store: ProjectContextStore,
    project_id: str,
    provider: str,
    session_meta: dict[str, object] | None,
    fallback_snapshot: dict[str, str],
) -> None:
    provider_sessions = project_provider_sessions(
        project_id=project_id,
        workspace_path=str(session_meta.get("cwd") or "") if session_meta else "",
        context_store=context_store,
    )
    messages = [str(item).strip() for item in (session_meta.get("messages", []) if session_meta else []) if str(item).strip()]
    extracted = extract_project_memory_from_messages(messages, fallback_snapshot=fallback_snapshot)
    focus = str(extracted.get("focus") or fallback_snapshot.get("focus") or "").strip()
    recent_context = str(extracted.get("recent_context") or fallback_snapshot.get("recent_context") or "").strip()
    summary_lines: list[str] = []
    if focus:
        summary_lines.append(f"Task: {focus}")
    if recent_context:
        summary_lines.append(f"Recent: {recent_context}")
    elif fallback_snapshot.get("memory"):
        summary_lines.append(f"Recent: {fallback_snapshot['memory']}")
    if not summary_lines:
        summary_lines.append("Task: Native project session active")
    active_task_id = ""
    if focus:
        active_task_id = sha1(f"{project_id}:{provider}:{focus}".encode("utf-8")).hexdigest()[:12]
    native_session_key = f"local-native:{project_id}:root"
    session_store.upsert_workspace_session(
        session_key=native_session_key,
        workspace_id=project_id,
        active_task_id=active_task_id or None,
        executor_session_id=provider_sessions.get(provider) or str(session_meta.get("session_id") or "") if session_meta else provider_sessions.get(provider),
        claude_session_id=provider_sessions.get("claude"),
        codex_session_id=provider_sessions.get("codex"),
        phase="discussion",
        conversation_summary="\n".join(summary_lines),
        swarm_state_json="",
        escalations_json="[]",
    )


def backfill_workspace_provider_sessions(
    *,
    context_store: ProjectContextStore,
    project_id: str,
    workspace_path: str,
    fallback_snapshot: dict[str, str],
    consolidate_project_memory_artifacts_cb,
) -> dict[str, str]:
    adopted: dict[str, str] = {}
    for provider in ("claude", "codex"):
        existing = context_store.get_provider_binding(project_id, provider)
        if existing:
            adopted[provider] = existing
            continue
        session_meta = select_postrun_session(
            provider=provider,
            workspace_path=workspace_path,
            before={},
            preferred_session_id=None,
        )
        if session_meta is None:
            continue
        record_provider_binding_and_memory(
            context_store=context_store,
            project_id=project_id,
            provider=provider,
            workspace_path=workspace_path,
            session_meta=session_meta,
            fallback_snapshot=fallback_snapshot,
            consolidate_project_memory_artifacts_cb=consolidate_project_memory_artifacts_cb,
        )
        session_id = str(session_meta.get("session_id") or "").strip()
        if session_id:
            adopted[provider] = session_id
    return adopted


def extract_project_memory_from_messages(
    messages: list[str],
    *,
    fallback_snapshot: dict[str, str],
) -> dict[str, object]:
    filtered = [item for item in messages if is_meaningful_project_memory_message(item)]
    user_messages = [
        item.removeprefix("user:").strip()
        for item in filtered
        if item.startswith("user:")
    ]
    assistant_messages = [
        item.removeprefix("assistant:").strip()
        for item in filtered
        if item.startswith("assistant:")
    ]
    hints = filtered[-2:]
    focus = user_messages[0] if user_messages else (fallback_snapshot.get("focus") or "")
    recent_context_parts: list[str] = []
    if assistant_messages:
        recent_context_parts.append(assistant_messages[-1])
    elif filtered:
        recent_context_parts.append(filtered[-1])
    if user_messages and user_messages[-1] != focus:
        latest_part = recent_context_parts[-1] if recent_context_parts else ""
        normalized_latest = ProjectContextStore._strip_memory_label(latest_part)
        normalized_user = ProjectContextStore._strip_memory_label(user_messages[-1])
        if normalized_user != normalized_latest:
            recent_context_parts.append(user_messages[-1])
    recent_context = " | ".join(part for part in recent_context_parts if part).strip()
    if not recent_context:
        recent_context = fallback_snapshot.get("recent_context") or ""
    memory_parts: list[str] = []
    if focus:
        memory_parts.append(f"Task: {focus}")
    if recent_context:
        memory_parts.append(f"State: {recent_context}")
    if assistant_messages:
        memory_parts.append(f"Latest result: {assistant_messages[-1]}")
    memory = " | ".join(part for part in memory_parts[:3]).strip()
    if not memory:
        memory = fallback_snapshot.get("memory") or ""
    return {
        "focus": focus,
        "recent_context": recent_context,
        "memory": memory,
        "recent_hints": hints,
        "fallback_only": not filtered,
    }


def is_meaningful_project_memory_message(text: str) -> bool:
    normalized = " ".join((text or "").split()).strip()
    if not normalized:
        return False
    if is_meta_project_memory_message(normalized):
        return False
    content = normalized
    if normalized.startswith("user:") or normalized.startswith("assistant:"):
        _, _, content = normalized.partition(":")
        content = content.strip()
    if len(content) < 6:
        return False
    low_signal = {"继续", "好的", "ok", "okay", "hi", "hello", "收到", "看下", "看看", "嗯", "是的"}
    return content.lower() not in low_signal


def is_meta_project_memory_message(text: str) -> bool:
    normalized = " ".join((text or "").split()).strip().lower()
    if not normalized:
        return False
    patterns = (
        "project summary for this session",
        "当前是新对话吗",
        "这是新对话吗",
        "有之前的记忆吗",
        "有记忆吗",
        "项目记忆",
        "新对话",
        "之前的记忆",
        "有之前对话",
        "is this a new conversation",
        "is this a new chat",
        "do you have memory",
        "do you remember",
        "project memory",
        "new conversation",
        "new chat",
    )
    return any(pattern in normalized for pattern in patterns)


def run_local_native(
    *,
    provider: str,
    project: str | None,
    pick_startup_workspace_cb,
    workspace_path_is_enterable_cb,
    auto_prepare_openviking_project_cb,
    consolidate_project_memory_artifacts_cb,
    read_openviking_overview_cb=read_openviking_overview,
) -> int:
    store = SessionStore()
    selected_workspace = project
    interactive = sys.stdin.isatty()
    while True:
        if not selected_workspace and interactive:
            print("Select a project before entering the native agent CLI.")
            selected_workspace = pick_startup_workspace_cb(store=store, require_path=True)
        workspace = resolve_workspace_record(store=store, workspace_id=selected_workspace, provider=provider)
        if selected_workspace and workspace is None:
            print(f"Unknown workspace: {selected_workspace}", file=sys.stderr)
            return 2
        if workspace is None or workspace_path_is_enterable_cb(workspace.path):
            break
        message = (
            f"Workspace `{workspace.workspace_id}` has no enterable path.\n"
            "Set a valid local project path before entering native CLI."
        )
        if not interactive or project:
            print(message, file=sys.stderr)
            return 2
        print(message)
        selected_workspace = None

    work_dir = workspace.path if workspace and workspace.path else os.getcwd()
    env = os.environ.copy()
    clear_project_runtime_env(env)
    postrun_before: dict[str, dict[str, object]] = {}
    fallback_snapshot: dict[str, str] = {
        "focus": "",
        "recent_context": "",
        "memory": "",
        "recent_hints": [],
    }
    if workspace is not None:
        context_store = ProjectContextStore()
        project_row = context_store.get_project(workspace.workspace_id)
        project_summary = project_row.summary if project_row is not None else ""
        auto_prepare_openviking_project_cb(workspace.workspace_id)
        env["ASH_ACTIVE_WORKSPACE"] = workspace.workspace_id
        env["ASH_PROJECT_PATH"] = work_dir
        env["CCB_WORK_DIR"] = work_dir
        env["CCB_RUN_DIR"] = work_dir
        env["PWD"] = work_dir
        snapshot = context_store.build_memory_snapshot(workspace.path)
        fallback_snapshot = snapshot
        inject_project_memory_env(
            env,
            workspace_path=workspace.path,
            context_store=context_store,
            snapshot=snapshot,
            read_openviking_overview_cb=read_openviking_overview_cb,
        )
        bootstrap_prompt = build_project_summary_prompt(
            workspace_id=workspace.workspace_id,
            work_dir=work_dir,
            summary=project_summary,
            snapshot=snapshot,
            driver_provider=provider,
            read_openviking_overview_cb=read_openviking_overview_cb,
        )
        provider_sessions = project_provider_sessions(
            project_id=workspace.workspace_id,
            workspace_path=workspace.path,
            context_store=context_store,
        )
        if not provider_sessions:
            backfill_workspace_provider_sessions(
                context_store=context_store,
                project_id=workspace.workspace_id,
                workspace_path=workspace.path,
                fallback_snapshot=snapshot,
                consolidate_project_memory_artifacts_cb=consolidate_project_memory_artifacts_cb,
            )
            provider_sessions = project_provider_sessions(
                project_id=workspace.workspace_id,
                workspace_path=workspace.path,
                context_store=context_store,
            )
        if provider_sessions.get("claude"):
            env["ASH_CLAUDE_SESSION_ID"] = provider_sessions["claude"]
        if provider_sessions.get("codex"):
            env["ASH_CODEX_SESSION_ID"] = provider_sessions["codex"]
        resume_session_id = provider_sessions.get(provider)
        session_mode = "resume-project-context" if resume_session_id else "fresh-project-context"
        if resume_session_id:
            env["ASH_PROVIDER_SESSION_ID"] = resume_session_id
        print_project_entry_view(
            provider=provider,
            workspace_id=workspace.workspace_id,
            work_dir=work_dir,
            project_summary=project_summary,
            snapshot=snapshot,
            resume_session_id=resume_session_id,
        )
        if provider == "codex":
            running_session = find_running_codex_session(session_id=resume_session_id, work_dir=work_dir)
            if running_session is not None:
                print(
                    "[agent-swarm-hub] detected existing codex process; "
                    f"skip duplicate launch (pid={running_session['pid']})"
                )
                return 0
        if interactive:
            confirm_project_entry(provider=provider)
        inject_project_identity_env(
            env,
            workspace_id=workspace.workspace_id,
            work_dir=work_dir,
            provider=provider,
            provider_session_id=resume_session_id,
            session_mode=session_mode,
        )
        postrun_before = collect_workspace_provider_sessions(provider, workspace.path)
    else:
        context_store = None
        resume_session_id = None
        bootstrap_prompt = ""
        env["PWD"] = work_dir
        print(f"[agent-swarm-hub] entering native {provider} CLI in temporary mode")
        print(f"[agent-swarm-hub] path={work_dir}")
        inject_project_identity_env(
            env,
            workspace_id=None,
            work_dir=work_dir,
            provider=provider,
            provider_session_id=None,
            session_mode="temporary",
        )

    argv = provider_launch_argv(
        provider=provider,
        command=provider_command(provider),
        session_id=resume_session_id,
        work_dir=work_dir,
        bootstrap_prompt=bootstrap_prompt,
    )
    return_code = 0
    try:
        result = subprocess.run(argv, env=env, cwd=work_dir, check=False)
        return_code = int(result.returncode)
    except KeyboardInterrupt:
        print()
        print(
            f"[agent-swarm-hub] interrupt received; finalizing project memory for `{workspace.workspace_id}`..."
            if workspace is not None
            else "[agent-swarm-hub] interrupt received; exiting native session..."
        )
        return_code = 130
    if workspace is not None and context_store is not None:
        session_meta = select_postrun_session(
            provider=provider,
            workspace_path=workspace.path,
            before=postrun_before,
            preferred_session_id=resume_session_id,
        )
        record_provider_binding_and_memory(
            context_store=context_store,
            project_id=workspace.workspace_id,
            provider=provider,
            workspace_path=workspace.path,
            session_meta=session_meta,
            fallback_snapshot=fallback_snapshot,
            consolidate_project_memory_artifacts_cb=consolidate_project_memory_artifacts_cb,
        )
        sync_native_workspace_runtime(
            session_store=store,
            context_store=context_store,
            project_id=workspace.workspace_id,
            provider=provider,
            session_meta=session_meta,
            fallback_snapshot=fallback_snapshot,
        )
    return return_code
