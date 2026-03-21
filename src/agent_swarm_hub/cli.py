from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from hashlib import sha1
from pathlib import Path

from .adapter import CCConnectAdapter
from .config import RuntimeConfig, apply_runtime_env, load_env_file
from .dashboard import serve_dashboard
from .executor import build_executor_for_config
from .lark_ws_runner import LarkWebSocketRunner
from .project_context import ProjectContextStore
from .paths import project_session_db_path, projects_root, provider_command
from .remote import RemoteMessage, RemotePlatform
from .session_store import SessionStore, WorkspaceRecord
from .telegram_polling import TelegramPollingRunner
from .telegram_service import TelegramService

ADD_PROJECT_SENTINEL = "__add_project__"


def _local_message(*, chat_id: str, user_id: str, text: str) -> RemoteMessage:
    return RemoteMessage(
        platform=RemotePlatform.LOCAL,
        chat_id=chat_id,
        user_id=user_id,
        text=text,
    )


def _resolve_workspace_selection(selection: str, workspaces: list[WorkspaceRecord]) -> str | None:
    raw = selection.strip()
    if not raw:
        return workspaces[0].workspace_id if workspaces else None
    if raw.lower() in {"add", "new", "add-project"}:
        return ADD_PROJECT_SENTINEL
    if raw.lower() in {"temporary", "temp"}:
        return None
    if raw.isdigit():
        index = int(raw) - 1
        if 0 <= index < len(workspaces):
            return workspaces[index].workspace_id
        if index == len(workspaces):
            return ADD_PROJECT_SENTINEL
        return ""
    normalized = raw.casefold()
    for workspace in workspaces:
        if normalized in {workspace.workspace_id.casefold(), workspace.title.casefold()}:
            return workspace.workspace_id
    return ""


def _shared_projects_as_workspaces() -> list[WorkspaceRecord]:
    contexts = ProjectContextStore().list_projects()
    workspaces: list[WorkspaceRecord] = []
    for context in contexts:
        workspaces.append(
            WorkspaceRecord(
                workspace_id=context.project_id,
                title=context.title,
                path=context.workspace_path,
                backend="claude",
                transport="direct",
                created_at="",
                updated_at="",
            )
        )
    return workspaces


def _workspace_path_is_enterable(path: str | None) -> bool:
    if not (path or "").strip():
        return False
    try:
        return Path(path).expanduser().is_dir()
    except OSError:
        return False


def _invocation_dir() -> Path:
    raw = (os.getenv("ASH_INVOKE_DIR") or os.getenv("PWD") or os.getcwd()).strip()
    try:
        return Path(raw).expanduser().resolve()
    except OSError:
        return Path.cwd()


def _project_slug(title: str) -> str:
    project_id = title.strip().lower().replace(" ", "-")
    keep = [ch for ch in project_id if ch.isalnum() or ch in {"-", "_", "."}]
    return "".join(keep) or "default"


def _new_project_workspace_path(title: str) -> Path:
    workspace_path = projects_root() / _project_slug(title)
    return workspace_path.expanduser().resolve()


def _upsert_project_workspace(*, store: SessionStore, title: str, workspace_path: Path) -> WorkspaceRecord:
    project_id = _project_slug(title)
    db_path = project_session_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_path.mkdir(parents=True, exist_ok=True)
    summary = f"Project: {project_id}\nWorkspace: {workspace_path}"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                project_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                workspace_path TEXT NOT NULL DEFAULT '',
                profile TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary, created_at, updated_at)
            VALUES (?, ?, ?, '', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(project_id) DO UPDATE SET
                title=excluded.title,
                workspace_path=excluded.workspace_path,
                updated_at=CURRENT_TIMESTAMP
            """,
            (project_id, title.strip() or project_id, str(workspace_path), summary),
        )
        conn.commit()
    store.upsert_workspace(
        workspace_id=project_id,
        title=title.strip() or project_id,
        path=str(workspace_path),
        backend="claude",
        transport="direct",
    )
    context_store = ProjectContextStore(str(db_path))
    context_store.sync_project_memory_file(project_id)
    context_store.sync_project_skill_file(project_id)
    return store.get_workspace(project_id) or WorkspaceRecord(
        workspace_id=project_id,
        title=title.strip() or project_id,
        path=str(workspace_path),
        backend="claude",
        transport="direct",
        created_at="",
        updated_at="",
    )


def _pick_startup_workspace(*, store: SessionStore, require_path: bool = False) -> str | None:
    workspaces = _shared_projects_as_workspaces() or store.list_workspaces()
    hidden_count = 0
    if require_path:
        hidden_count = sum(1 for workspace in workspaces if not _workspace_path_is_enterable(workspace.path))
        workspaces = [workspace for workspace in workspaces if _workspace_path_is_enterable(workspace.path)]
    if not workspaces:
        if require_path:
            print("No workspaces with an enterable path were found.")
            title = input("New project name (leave blank for temporary): ").strip()
            if not title:
                print("Temporary mode selected.")
                return None
            workspace = _upsert_project_workspace(
                store=store,
                title=title,
                workspace_path=_new_project_workspace_path(title),
            )
            print(f"Added project `{workspace.workspace_id}` at {workspace.path}.")
            return workspace.workspace_id
        print("No workspaces found. Temporary chat selected.")
        return None

    print("Choose a project or temporary chat:")
    for index, workspace in enumerate(workspaces, start=1):
        print(f"{index}. {workspace.workspace_id} ({workspace.backend}/{workspace.transport})")
    print(f"{len(workspaces) + 1}. add-project (create a new project directory)")
    print("Type temporary to start an unbound local chat.")

    prompt = "Select project by number/name, or type temporary"
    if workspaces:
        prompt += f" [Enter={workspaces[0].workspace_id}]"
    prompt += ": "

    while True:
        selection = input(prompt)
        resolved = _resolve_workspace_selection(selection, workspaces)
        if resolved == "":
            print("Unknown project selection. Choose an existing workspace or type temporary.")
            continue
        if resolved == ADD_PROJECT_SENTINEL:
            title = input("New project name: ").strip()
            if not title:
                print("Project name cannot be empty.")
                continue
            workspace = _upsert_project_workspace(
                store=store,
                title=title,
                workspace_path=_new_project_workspace_path(title),
            )
            print(f"Added project `{workspace.workspace_id}` at {workspace.path}.")
            return workspace.workspace_id
        if resolved is None:
            print("Temporary chat selected. Start chatting directly, or use /use <workspace> later to move into a project.")
            return None
        return resolved


def _run_local_chat(*, provider: str, chat_id: str, user_id: str, project: str | None) -> int:
    store = SessionStore()
    adapter = CCConnectAdapter(
        executor=build_executor_for_config(
            mode=provider,
            transport="auto",
        ),
        store=store,
    )
    local_session_key = _local_message(chat_id=chat_id, user_id=user_id, text="").session_key
    checkpoint_interval = max(1, int((os.getenv("ASH_MEMORY_CHECKPOINT_INTERVAL") or "8").strip() or "8"))
    checkpoint_interval_s = max(30, int((os.getenv("ASH_MEMORY_CHECKPOINT_SECONDS") or "600").strip() or "600"))
    handled_messages = 0
    last_checkpoint_at = time.monotonic()
    if project:
        response = adapter.handle_message(_local_message(chat_id=chat_id, user_id=user_id, text=f"/use {project}"))
        print(response.text)
    elif sys.stdin.isatty():
        print(adapter.handle_message(_local_message(chat_id=chat_id, user_id=user_id, text="/projects")).text)
        selected_workspace = _pick_startup_workspace(store=store)
        if selected_workspace:
            response = adapter.handle_message(_local_message(chat_id=chat_id, user_id=user_id, text=f"/use {selected_workspace}"))
            print(response.text)
        else:
            print("Temporary local chat is ready. Just start chatting.")
        print("Chat naturally. Use /help for advanced commands or /quit to exit the current project/chat.")
        print("Complex tasks will automatically enter planning / coordinated swarm execution when needed.")

    while True:
        try:
            line = input("> ")
        except KeyboardInterrupt:
            print()
            _finalize_local_chat_memory(adapter=adapter, chat_id=chat_id, user_id=user_id, session_key=local_session_key)
            return 130
        except EOFError:
            print()
            _finalize_local_chat_memory(adapter=adapter, chat_id=chat_id, user_id=user_id, session_key=local_session_key)
            return 0
        text = line.strip()
        if not text:
            continue
        if text in {"/quit", "/exit"}:
            response = adapter.handle_message(_local_message(chat_id=chat_id, user_id=user_id, text="/quit"))
            print(response.text)
            return 0
        response = adapter.handle_message(_local_message(chat_id=chat_id, user_id=user_id, text=text))
        print(response.text)
        handled_messages += 1
        now = time.monotonic()
        if handled_messages % checkpoint_interval == 0 or (now - last_checkpoint_at) >= checkpoint_interval_s:
            _checkpoint_local_chat_memory(adapter=adapter, chat_id=chat_id, user_id=user_id, session_key=local_session_key)
            last_checkpoint_at = now


def _checkpoint_local_chat_memory(*, adapter: CCConnectAdapter, chat_id: str, user_id: str, session_key: str) -> None:
    workspace_id = adapter._get_bound_workspace(_local_message(chat_id=chat_id, user_id=user_id, text=""))  # type: ignore[attr-defined]
    if not workspace_id:
        return
    try:
        adapter._sync_project_memory(session_key=session_key, workspace_id=workspace_id)  # type: ignore[attr-defined]
        _consolidate_bound_workspace_memory(adapter=adapter, session_key=session_key, workspace_id=workspace_id)
    except Exception:
        return
    print(f"[agent-swarm-hub] memory checkpoint synced for `{workspace_id}`")


def _finalize_local_chat_memory(*, adapter: CCConnectAdapter, chat_id: str, user_id: str, session_key: str) -> None:
    workspace_id = adapter._get_bound_workspace(_local_message(chat_id=chat_id, user_id=user_id, text=""))  # type: ignore[attr-defined]
    if not workspace_id:
        return
    try:
        response = adapter.handle_message(_local_message(chat_id=chat_id, user_id=user_id, text="/quit"))
        print(response.text)
    except Exception:
        try:
            adapter._sync_project_memory(session_key=session_key, workspace_id=workspace_id)  # type: ignore[attr-defined]
            _consolidate_bound_workspace_memory(adapter=adapter, session_key=session_key, workspace_id=workspace_id)
            print(f"[agent-swarm-hub] finalized project memory for `{workspace_id}`")
        except Exception:
            return


def _consolidate_bound_workspace_memory(*, adapter: CCConnectAdapter, session_key: str, workspace_id: str) -> None:
    project_id = adapter._resolve_shared_project_id(workspace_id)  # type: ignore[attr-defined]
    if not project_id:
        return
    memory_key = adapter._memory_key(session_key, workspace_id)  # type: ignore[attr-defined]
    recent_rows = adapter.store.list_recent_messages(memory_key, limit=6)
    recent_messages = [f"{row['role']}: {row['text']}" for row in recent_rows if (row["text"] or "").strip()]
    workspace_session = adapter.store.get_workspace_session(session_key, workspace_id)
    live_summary = workspace_session.conversation_summary if workspace_session and workspace_session.conversation_summary else ""
    adapter.project_context_store.consolidate_project_memory(
        project_id,
        live_summary=live_summary,
        recent_messages=recent_messages,
    )


def _resolve_workspace_record(*, store: SessionStore, workspace_id: str | None, provider: str) -> WorkspaceRecord | None:
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


def _latest_provider_session(*, project_id: str, provider: str, workspace_path: str | None, context_store: ProjectContextStore | None = None) -> str | None:
    store = context_store or ProjectContextStore()
    bound_session_id = store.get_provider_binding(project_id, provider)
    if bound_session_id and _provider_session_exists(provider=provider, session_id=bound_session_id):
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
            if session_id and _provider_session_matches_workspace(
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


def _provider_session_matches_workspace(*, provider: str, session_id: str, workspace_path: str | None) -> bool:
    if provider != "codex" or not (workspace_path or "").strip():
        return True
    session_file = _find_codex_session_file(session_id)
    if session_file is None:
        return False
    session_cwd = _read_codex_session_cwd(session_file)
    if not session_cwd:
        return False
    try:
        workspace = Path(workspace_path).expanduser().resolve()
        session_path = Path(session_cwd).expanduser().resolve()
    except OSError:
        return False
    return session_path == workspace or workspace in session_path.parents


def _provider_session_exists(*, provider: str, session_id: str) -> bool:
    if not session_id:
        return False
    if provider == "codex":
        return _find_codex_session_file(session_id) is not None
    return True


def _find_codex_session_file(session_id: str) -> Path | None:
    root = Path.home() / ".codex" / "sessions"
    if not root.exists():
        return None
    matches = list(root.glob(f"**/*{session_id}*.jsonl"))
    return matches[0] if matches else None


def _read_codex_session_cwd(session_file: Path) -> str:
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


def _project_provider_sessions(*, project_id: str, workspace_path: str | None, context_store: ProjectContextStore | None = None) -> dict[str, str]:
    store = context_store or ProjectContextStore()
    sessions: dict[str, str] = {}
    for provider in ("claude", "codex"):
        session_id = _latest_provider_session(
            project_id=project_id,
            provider=provider,
            workspace_path=workspace_path,
            context_store=store,
        )
        if session_id:
            sessions[provider] = session_id
    return sessions


def _build_memory_summary(*, snapshot: dict[str, str]) -> str:
    parts: list[str] = []
    if snapshot.get("focus"):
        parts.append(f"focus={snapshot['focus']}")
    if snapshot.get("recent_context"):
        parts.append(f"current_state={snapshot['recent_context']}")
    elif snapshot.get("memory"):
        parts.append(f"memory={snapshot['memory']}")
    hints = [item for item in snapshot.get("recent_hints", []) if item]
    if hints:
        parts.append(f"hints={'; '.join(hints[:2])}")
    return " | ".join(parts)


def _project_summary_field(summary: str, prefix: str) -> str:
    return ProjectContextStore._summary_field(summary, prefix)


def _build_project_summary_prompt(*, workspace_id: str, work_dir: str, summary: str, snapshot: dict[str, str], driver_provider: str) -> str:
    focus = _project_summary_field(summary, "Current focus:") or snapshot.get("focus") or ""
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
    if brief["recent_context"]:
        lines.append(f"- Current State: {brief['recent_context']}")
    if brief["next_step"]:
        lines.append(f"- Next Step: {brief['next_step']}")
    if brief["memory"]:
        lines.append(f"- Project Memory: {brief['memory']}")
    lines.extend(
        [
            "- Swarm Mode: complex tasks may automatically enter coordinated multi-agent execution",
            f"- Current Trigger: {provider_driver}",
            "- Swarm Orchestrator: claude (launched in tmux when coordination starts)",
            "- Coordination Roles: orchestrator=claude, planner=claude, executor=codex, reviewer=claude",
            "- Return Target: claude",
        ]
    )
    lines.append(f"- Read first: {work_dir}/PROJECT_MEMORY.md")
    lines.append(f"- Rules file: {work_dir}/PROJECT_SKILL.md")
    lines.append("Use these project files plus this summary as the project context for the session.")
    return "\n".join(lines)


def _print_project_entry_view(
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
    focus = _project_summary_field(project_summary, "Current focus:") or snapshot.get("focus") or ""
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


def _confirm_project_entry(*, provider: str) -> None:
    if str(os.getenv("ASH_AUTO_ENTER_NATIVE") or "").strip().lower() in {"1", "true", "yes", "on"}:
        print(f"[agent-swarm-hub] auto-enter native {provider} CLI")
        return
    print(f"Press Enter to enter native {provider} CLI...")
    input()


def _provider_launch_argv(
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


def _clear_project_runtime_env(env: dict[str, str]) -> None:
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
        "ASH_PROVIDER_SESSION_ID",
        "ASH_CLAUDE_SESSION_ID",
        "ASH_CODEX_SESSION_ID",
        "CCB_WORK_DIR",
        "CCB_RUN_DIR",
    ):
        env.pop(key, None)


def _inject_project_identity_env(
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


def _inject_project_memory_env(
    env: dict[str, str],
    *,
    workspace_path: str | None,
    context_store: ProjectContextStore | None = None,
    snapshot: dict[str, str] | None = None,
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
    return True


def _workspace_path_matches(workspace_path: str | None, candidate_cwd: str | None) -> bool:
    if not (workspace_path or "").strip() or not (candidate_cwd or "").strip():
        return False
    try:
        workspace = Path(workspace_path).expanduser().resolve()
        candidate = Path(candidate_cwd).expanduser().resolve()
    except OSError:
        return False
    return candidate == workspace or workspace in candidate.parents


def _extract_codex_history(session_id: str) -> list[str]:
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


def _extract_claude_text(content) -> str:
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


def _collect_codex_workspace_sessions(workspace_path: str | None) -> dict[str, dict[str, object]]:
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
        if not session_id or not _workspace_path_matches(workspace_path, cwd):
            continue
        stat = path.stat()
        sessions[session_id] = {
            "session_id": session_id,
            "cwd": cwd,
            "source_path": str(path),
            "sort_key": int(stat.st_mtime_ns),
            "last_used_at": str(int(stat.st_mtime)),
            "messages": _extract_codex_history(session_id),
        }
    return sessions


def _collect_claude_workspace_sessions(workspace_path: str | None) -> dict[str, dict[str, object]]:
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
                    text = _extract_claude_text(message.get("content"))
                    if text:
                        messages.append(f"{role}: {text}")
        except OSError:
            continue
        if not session_id or not _workspace_path_matches(workspace_path, cwd):
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


def _collect_workspace_provider_sessions(provider: str, workspace_path: str | None) -> dict[str, dict[str, object]]:
    if provider == "codex":
        return _collect_codex_workspace_sessions(workspace_path)
    if provider == "claude":
        return _collect_claude_workspace_sessions(workspace_path)
    return {}


def _select_postrun_session(
    *,
    provider: str,
    workspace_path: str | None,
    before: dict[str, dict[str, object]],
    preferred_session_id: str | None,
) -> dict[str, object] | None:
    after = _collect_workspace_provider_sessions(provider, workspace_path)
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


def _record_provider_binding_and_memory(
    *,
    context_store: ProjectContextStore,
    project_id: str,
    provider: str,
    workspace_path: str,
    session_meta: dict[str, object] | None,
    fallback_snapshot: dict[str, str],
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
    extracted = _extract_project_memory_from_messages(messages, fallback_snapshot=fallback_snapshot)
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
    _consolidate_project_memory_artifacts(
        context_store,
        project_id,
        live_summary=" | ".join(messages[-3:]) if messages else "",
        recent_messages=messages,
    )


def _sync_native_workspace_runtime(
    *,
    session_store: SessionStore,
    context_store: ProjectContextStore,
    project_id: str,
    provider: str,
    session_meta: dict[str, object] | None,
    fallback_snapshot: dict[str, str],
) -> None:
    provider_sessions = _project_provider_sessions(
        project_id=project_id,
        workspace_path=str(session_meta.get("cwd") or "") if session_meta else "",
        context_store=context_store,
    )
    messages = [str(item).strip() for item in (session_meta.get("messages", []) if session_meta else []) if str(item).strip()]
    extracted = _extract_project_memory_from_messages(messages, fallback_snapshot=fallback_snapshot)
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
    phase = "discussion"
    native_session_key = f"local-native:{project_id}:root"
    session_store.upsert_workspace_session(
        session_key=native_session_key,
        workspace_id=project_id,
        active_task_id=active_task_id or None,
        executor_session_id=provider_sessions.get(provider) or str(session_meta.get("session_id") or "") if session_meta else provider_sessions.get(provider),
        claude_session_id=provider_sessions.get("claude"),
        codex_session_id=provider_sessions.get("codex"),
        phase=phase,
        conversation_summary="\n".join(summary_lines),
        swarm_state_json="",
        escalations_json="[]",
    )


def _backfill_workspace_provider_sessions(
    *,
    context_store: ProjectContextStore,
    project_id: str,
    workspace_path: str,
    fallback_snapshot: dict[str, str],
) -> dict[str, str]:
    adopted: dict[str, str] = {}
    for provider in ("claude", "codex"):
        existing = context_store.get_provider_binding(project_id, provider)
        if existing:
            adopted[provider] = existing
            continue
        session_meta = _select_postrun_session(
            provider=provider,
            workspace_path=workspace_path,
            before={},
            preferred_session_id=None,
        )
        if session_meta is None:
            continue
        _record_provider_binding_and_memory(
            context_store=context_store,
            project_id=project_id,
            provider=provider,
            workspace_path=workspace_path,
            session_meta=session_meta,
            fallback_snapshot=fallback_snapshot,
        )
        session_id = str(session_meta.get("session_id") or "").strip()
        if session_id:
            adopted[provider] = session_id
    return adopted


def _extract_project_memory_from_messages(
    messages: list[str],
    *,
    fallback_snapshot: dict[str, str],
) -> dict[str, object]:
    filtered = [item for item in messages if _is_meaningful_project_memory_message(item)]
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


def _is_meaningful_project_memory_message(text: str) -> bool:
    normalized = " ".join((text or "").split()).strip()
    if not normalized:
        return False
    if _is_meta_project_memory_message(normalized):
        return False
    content = normalized
    if normalized.startswith("user:") or normalized.startswith("assistant:"):
        _, _, content = normalized.partition(":")
        content = content.strip()
    if len(content) < 6:
        return False
    low_signal = {"继续", "好的", "ok", "okay", "hi", "hello", "收到", "看下", "看看", "嗯", "是的"}
    return content.lower() not in low_signal


def _is_meta_project_memory_message(text: str) -> bool:
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


def _project_sessions_current(project_id: str) -> int:
    store = ProjectContextStore()
    project = store.get_project(project_id)
    if project is None:
        print(f"Unknown project: {project_id}", file=sys.stderr)
        return 2
    current = store.get_current_project_sessions(project_id)
    print(f"Project: {project_id}")
    if not current:
        print("No bound provider sessions.")
        return 0
    for provider in sorted(current):
        print(f"{provider}: {current[provider]}")
    return 0


def _project_sessions_list(project_id: str, provider: str | None) -> int:
    store = ProjectContextStore()
    project = store.get_project(project_id)
    if project is None:
        print(f"Unknown project: {project_id}", file=sys.stderr)
        return 2
    current = store.get_current_project_sessions(project_id)
    rows = store.list_project_sessions(project_id, provider=provider, include_archived=True)
    print(f"Project: {project_id}")
    if not rows:
        print("No project sessions recorded.")
        return 0
    for row in rows:
        marker = "current" if current.get(row["provider"]) == row["session_id"] else row["status"]
        title = row["title"] or row["summary"] or ""
        print(
            f"{row['provider']} | {marker} | {row['session_id']} | "
            f"{title[:80]} | {row['last_used_at']}"
        )
    return 0


def _project_sessions_use(project_id: str, provider: str, session_id: str) -> int:
    store = ProjectContextStore()
    project = store.get_project(project_id)
    if project is None:
        print(f"Unknown project: {project_id}", file=sys.stderr)
        return 2
    rows = store.list_project_sessions(project_id, provider=provider, include_archived=True)
    if not any(row["session_id"] == session_id for row in rows):
        print(f"Session not recorded for project `{project_id}`: {provider}/{session_id}", file=sys.stderr)
        return 2
    store.set_project_session_status(provider, session_id, "active")
    store.set_provider_binding(project_id, provider, session_id)
    _sync_project_memory_artifacts(store, project_id)
    print(f"Current {provider} session for `{project_id}` set to {session_id}.")
    return 0


def _sync_project_memory_artifacts(store: ProjectContextStore, project_id: str) -> None:
    store.sync_project_summary(project_id)
    store.sync_project_memory_file(project_id)
    store.sync_project_skill_file(project_id)


def _consolidate_project_memory_artifacts(
    store: ProjectContextStore,
    project_id: str,
    *,
    live_summary: str = "",
    recent_messages: list[str] | None = None,
) -> bool:
    ok = store.consolidate_project_memory(
        project_id,
        live_summary=live_summary,
        recent_messages=recent_messages,
    )
    _sync_project_memory_artifacts(store, project_id)
    return ok


def _project_sessions_sync_memory(project_id: str | None, *, sync_all: bool) -> int:
    store = ProjectContextStore()
    if sync_all:
        projects = store.list_projects()
        if not projects:
            print("No projects recorded.")
            return 0
        for project in projects:
            _consolidate_project_memory_artifacts(store, project.project_id)
            print(f"Synced project memory for `{project.project_id}`.")
        return 0
    if not project_id:
        print("Provide a project id or use --all.", file=sys.stderr)
        return 2
    project = store.get_project(project_id)
    if project is None:
        print(f"Unknown project: {project_id}", file=sys.stderr)
        return 2
    _consolidate_project_memory_artifacts(store, project_id)
    print(f"Synced project memory for `{project_id}`.")
    return 0


def _project_sessions_remove_project(project_id: str) -> int:
    project_id = (project_id or "").strip()
    if not project_id:
        print("Provide a project id.", file=sys.stderr)
        return 2
    project_store = ProjectContextStore()
    session_store = SessionStore()
    project_exists = project_store.get_project(project_id) is not None
    workspace_exists = session_store.get_workspace(project_id) is not None
    if not project_exists and not workspace_exists:
        print(f"Unknown project/workspace: {project_id}", file=sys.stderr)
        return 2
    session_store.remove_workspace(project_id)
    project_store.remove_project(project_id)
    print(f"Removed stale project records for `{project_id}`.")
    return 0


def _run_local_native(*, provider: str, project: str | None) -> int:
    store = SessionStore()
    selected_workspace = project
    interactive = sys.stdin.isatty()
    while True:
        if not selected_workspace and interactive:
            print("Select a project before entering the native agent CLI.")
            selected_workspace = _pick_startup_workspace(store=store, require_path=True)
        workspace = _resolve_workspace_record(store=store, workspace_id=selected_workspace, provider=provider)
        if selected_workspace and workspace is None:
            print(f"Unknown workspace: {selected_workspace}", file=sys.stderr)
            return 2
        if workspace is None or _workspace_path_is_enterable(workspace.path):
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
    _clear_project_runtime_env(env)
    postrun_before: dict[str, dict[str, object]] = {}
    fallback_snapshot: dict[str, str] = {
        "focus": "",
        "recent_context": "",
        "memory": "",
        "recent_hints": [],
    }
    if workspace is not None:
        context_store = ProjectContextStore()
        project = context_store.get_project(workspace.workspace_id)
        project_summary = project.summary if project is not None else ""
        env["ASH_ACTIVE_WORKSPACE"] = workspace.workspace_id
        env["ASH_PROJECT_PATH"] = work_dir
        env["CCB_WORK_DIR"] = work_dir
        env["CCB_RUN_DIR"] = work_dir
        env["PWD"] = work_dir
        snapshot = context_store.build_memory_snapshot(workspace.path)
        fallback_snapshot = snapshot
        _inject_project_memory_env(env, workspace_path=workspace.path, context_store=context_store, snapshot=snapshot)
        bootstrap_prompt = _build_project_summary_prompt(
            workspace_id=workspace.workspace_id,
            work_dir=work_dir,
            summary=project_summary,
            snapshot=snapshot,
            driver_provider=provider,
        )
        provider_sessions = _project_provider_sessions(
            project_id=workspace.workspace_id,
            workspace_path=workspace.path,
            context_store=context_store,
        )
        if not provider_sessions:
            _backfill_workspace_provider_sessions(
                context_store=context_store,
                project_id=workspace.workspace_id,
                workspace_path=workspace.path,
                fallback_snapshot=snapshot,
            )
            provider_sessions = _project_provider_sessions(
                project_id=workspace.workspace_id,
                workspace_path=workspace.path,
                context_store=context_store,
            )
        if provider_sessions.get("claude"):
            env["ASH_CLAUDE_SESSION_ID"] = provider_sessions["claude"]
        if provider_sessions.get("codex"):
            env["ASH_CODEX_SESSION_ID"] = provider_sessions["codex"]
        resume_session_id = provider_sessions.get(provider)
        if resume_session_id:
            env["ASH_PROVIDER_SESSION_ID"] = resume_session_id
            session_mode = "resume-project-context"
        else:
            session_mode = "fresh-project-context"
        _print_project_entry_view(
            provider=provider,
            workspace_id=workspace.workspace_id,
            work_dir=work_dir,
            project_summary=project_summary,
            snapshot=snapshot,
            resume_session_id=resume_session_id,
        )
        if interactive:
            _confirm_project_entry(provider=provider)
        _inject_project_identity_env(
            env,
            workspace_id=workspace.workspace_id,
            work_dir=work_dir,
            provider=provider,
            provider_session_id=resume_session_id,
            session_mode=session_mode,
        )
        postrun_before = _collect_workspace_provider_sessions(provider, workspace.path)
    else:
        context_store = None
        resume_session_id = None
        bootstrap_prompt = ""
        env["PWD"] = work_dir
        print(f"[agent-swarm-hub] entering native {provider} CLI in temporary mode")
        print(f"[agent-swarm-hub] path={work_dir}")
        _inject_project_identity_env(
            env,
            workspace_id=None,
            work_dir=work_dir,
            provider=provider,
            provider_session_id=None,
            session_mode="temporary",
        )

    os.chdir(work_dir)
    command = provider_command(provider)
    argv = _provider_launch_argv(
        provider=provider,
        command=command,
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
        print(f"[agent-swarm-hub] interrupt received; finalizing project memory for `{workspace.workspace_id}`..." if workspace is not None else "[agent-swarm-hub] interrupt received; exiting native session...")
        return_code = 130
    if workspace is not None and context_store is not None:
        session_meta = _select_postrun_session(
            provider=provider,
            workspace_path=workspace.path,
            before=postrun_before,
            preferred_session_id=resume_session_id,
        )
        _record_provider_binding_and_memory(
            context_store=context_store,
            project_id=workspace.workspace_id,
            provider=provider,
            workspace_path=workspace.path,
            session_meta=session_meta,
            fallback_snapshot=fallback_snapshot,
        )
        _sync_native_workspace_runtime(
            session_store=store,
            context_store=context_store,
            project_id=workspace.workspace_id,
            provider=provider,
            session_meta=session_meta,
            fallback_snapshot=fallback_snapshot,
        )
    return return_code


def _run_short_entry(*, mode: str) -> int:
    parser = argparse.ArgumentParser(description=f"{mode} shortcut entry")
    parser.add_argument("arg1", nargs="?", default=None)
    parser.add_argument("arg2", nargs="?", default=None)
    parser.add_argument(
        "--env-file",
        default=".env.local",
        help="Optional local env file to load before reading config",
    )
    args = parser.parse_args()
    load_env_file(args.env_file)
    apply_runtime_env()
    config = RuntimeConfig.from_env()
    provider, project = _resolve_short_provider_project(arg1=args.arg1, arg2=args.arg2, config=config)
    if mode == "chat":
        return _run_local_native(provider=provider, project=project)
    return _run_local_chat(
        provider=provider,
        chat_id="local-cli",
        user_id="local-user",
        project=project,
    )


def _print_main_menu() -> None:
    print("agent-swarm-hub")
    print("  chat [provider] [project]      enter native project chat")
    print("  swarm [provider] [project]     enter local swarm shell")
    print("  dash                           open local dashboard")
    print("  project-sessions ...           inspect or switch bound sessions")
    print("  lark-ws                        start Lark websocket listener")
    print("  telegram-poll                  run Telegram polling loop")
    print("")
    print("Examples:")
    print("  agent-swarm-hub chat codex agent-browser")
    print("  agent-swarm-hub swarm claude")
    print("  agent-swarm-hub dash")


def _resolve_short_provider_project(
    *,
    arg1: str | None,
    arg2: str | None,
    config: RuntimeConfig,
) -> tuple[str, str | None]:
    explicit_provider = (arg1 or "").strip().lower()
    if explicit_provider and explicit_provider not in {"codex", "claude"}:
        return (config.executor_mode or "codex").strip().lower(), arg1
    return explicit_provider or (config.executor_mode or "codex").strip().lower(), arg2


def _open_dashboard_url(*, host: str, port: int) -> None:
    subprocess.Popen(["open", f"http://{host}:{port}"])


def main() -> int:
    parser = argparse.ArgumentParser(description="agent-swarm-hub local runners")
    parser.add_argument(
        "--env-file",
        default=".env.local",
        help="Optional local env file to load before reading config",
    )
    subparsers = parser.add_subparsers(dest="command")

    lark_ws = subparsers.add_parser("lark-ws", help="Start the Lark websocket event listener")
    lark_ws.add_argument(
        "--print-config",
        action="store_true",
        help="Print effective Lark config and exit instead of starting the client",
    )
    telegram_poll = subparsers.add_parser("telegram-poll", help="Run Telegram polling for personal local use")
    telegram_poll.add_argument("--once", action="store_true", help="Process one polling cycle and exit")
    telegram_poll.add_argument("--offset", type=int, default=None, help="Optional Telegram update offset")
    telegram_poll.add_argument(
        "--print-config",
        action="store_true",
        help="Print effective Telegram config and exit instead of polling",
    )
    local_chat = subparsers.add_parser("local-chat", help="Run a local interactive chat using the same commands as remote chat")
    local_chat.add_argument(
        "--provider",
        default=None,
        help="Preferred provider for this local chat session (defaults to ASH_EXECUTOR or codex)",
    )
    local_chat.add_argument("--chat-id", default="local-cli", help="Stable local chat id for session persistence")
    local_chat.add_argument("--user-id", default="local-user", help="Local user id")
    local_chat.add_argument("--project", default=None, help="Optional project/workspace to bind immediately")
    local_native = subparsers.add_parser("local-native", help="Pick a project, then enter the native provider CLI")
    local_native.add_argument(
        "--provider",
        default=None,
        help="Native provider to launch after project selection (defaults to ASH_EXECUTOR or codex)",
    )
    local_native.add_argument("--project", default=None, help="Optional project/workspace to enter immediately")
    chat_short = subparsers.add_parser("chat", help="Shortcut for local-native")
    chat_short.add_argument("provider", nargs="?", default=None)
    chat_short.add_argument("project", nargs="?", default=None)
    swarm_short = subparsers.add_parser("swarm", help="Shortcut for local-chat")
    swarm_short.add_argument("provider", nargs="?", default=None)
    swarm_short.add_argument("project", nargs="?", default=None)
    dashboard = subparsers.add_parser("dashboard", help="Run a local read-only project dashboard")
    dashboard.add_argument("--host", default="127.0.0.1")
    dashboard.add_argument("--port", type=int, default=8765)
    dashboard.add_argument("--open", action="store_true", help="Open the dashboard URL in the local browser")
    dash_short = subparsers.add_parser("dash", help="Shortcut for dashboard")
    dash_short.add_argument("--host", default="127.0.0.1")
    dash_short.add_argument("--port", type=int, default=8765)
    dash_short.add_argument("--open", action="store_true", help="Open the dashboard URL in the local browser")
    project_sessions = subparsers.add_parser("project-sessions", help="Manage project-mapped native sessions")
    project_sessions_sub = project_sessions.add_subparsers(dest="project_sessions_command", required=True)
    project_sessions_current = project_sessions_sub.add_parser("current", help="Show current bound sessions for a project")
    project_sessions_current.add_argument("project")
    project_sessions_list = project_sessions_sub.add_parser("list", help="List recorded native sessions for a project")
    project_sessions_list.add_argument("project")
    project_sessions_list.add_argument("--provider", default=None)
    project_sessions_use = project_sessions_sub.add_parser("use", help="Set current bound session for a project")
    project_sessions_use.add_argument("project")
    project_sessions_use.add_argument("provider")
    project_sessions_use.add_argument("session_id")
    project_sessions_sync = project_sessions_sub.add_parser(
        "sync-memory",
        help="Rebuild structured project summary, PROJECT_MEMORY.md, and PROJECT_SKILL.md",
    )
    project_sessions_sync.add_argument("project", nargs="?")
    project_sessions_sync.add_argument("--all", action="store_true")
    project_sessions_remove = project_sessions_sub.add_parser(
        "remove-project",
        help="Remove a project/workspace and its stale runtime/session records",
    )
    project_sessions_remove.add_argument("project")

    args = parser.parse_args()
    if not args.command:
        _print_main_menu()
        return 0
    load_env_file(args.env_file)
    apply_runtime_env()

    if args.command == "lark-ws":
        config = RuntimeConfig.from_env().lark
        if args.print_config:
            print(
                {
                    "enabled": config.enabled,
                    "app_id": config.app_id,
                    "verify_token": config.verify_token,
                    "encrypt_key_configured": bool(config.encrypt_key),
                }
            )
            return 0

        runner = LarkWebSocketRunner.create(config)
        runner.run_forever()
        return 0
    if args.command == "telegram-poll":
        config = RuntimeConfig.from_env().telegram
        if args.print_config:
            print(
                {
                    "enabled": config.enabled,
                    "bot_token_configured": bool(config.bot_token),
                    "polling_timeout_s": config.polling_timeout_s,
                    "parse_mode": config.default_parse_mode or None,
                }
            )
            return 0

        polling = TelegramPollingRunner(TelegramService(config))
        if args.once:
            result = polling.run_once(offset=args.offset)
            print(
                {
                    "updates_seen": result.updates_seen,
                    "updates_processed": result.updates_processed,
                    "next_offset": result.next_offset,
                }
            )
            return 0

        polling.run_forever(offset=args.offset)
        return 0
    if args.command == "local-chat":
        config = RuntimeConfig.from_env()
        provider = (args.provider or config.executor_mode or "codex").strip().lower()
        return _run_local_chat(
            provider=provider,
            chat_id=args.chat_id,
            user_id=args.user_id,
            project=args.project,
        )
    if args.command == "local-native":
        config = RuntimeConfig.from_env()
        provider = (args.provider or config.executor_mode or "codex").strip().lower()
        return _run_local_native(provider=provider, project=args.project)
    if args.command == "chat":
        config = RuntimeConfig.from_env()
        provider, project = _resolve_short_provider_project(arg1=args.provider, arg2=args.project, config=config)
        return _run_local_native(provider=provider, project=project)
    if args.command == "swarm":
        config = RuntimeConfig.from_env()
        provider, project = _resolve_short_provider_project(arg1=args.provider, arg2=args.project, config=config)
        return _run_local_chat(
            provider=provider,
            chat_id="local-cli",
            user_id="local-user",
            project=project,
        )
    if args.command in {"dashboard", "dash"}:
        if args.open:
            _open_dashboard_url(host=args.host, port=args.port)
        serve_dashboard(host=args.host, port=args.port)
        return 0
    if args.command == "project-sessions":
        if args.project_sessions_command == "current":
            return _project_sessions_current(args.project)
        if args.project_sessions_command == "list":
            return _project_sessions_list(args.project, args.provider)
        if args.project_sessions_command == "use":
            return _project_sessions_use(args.project, args.provider.strip().lower(), args.session_id.strip())
        if args.project_sessions_command == "sync-memory":
            return _project_sessions_sync_memory(args.project, sync_all=args.all)
        if args.project_sessions_command == "remove-project":
            return _project_sessions_remove_project(args.project)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())


def ash_chat_main() -> int:
    return _run_short_entry(mode="chat")


def ash_swarm_main() -> int:
    return _run_short_entry(mode="swarm")
