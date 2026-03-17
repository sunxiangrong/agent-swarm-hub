from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from .adapter import CCConnectAdapter
from .config import RuntimeConfig, apply_runtime_env, load_env_file
from .executor import build_executor_for_config
from .lark_ws_runner import LarkWebSocketRunner
from .project_context import DEFAULT_PROJECT_SESSION_DB, ProjectContextStore
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


def _upsert_project_workspace(*, store: SessionStore, title: str, workspace_path: Path) -> WorkspaceRecord:
    project_id = title.strip().lower().replace(" ", "-")
    keep = [ch for ch in project_id if ch.isalnum() or ch in {"-", "_", "."}]
    project_id = "".join(keep) or "default"
    db_path = Path(os.getenv("ASH_PROJECT_SESSION_DB", "").strip() or DEFAULT_PROJECT_SESSION_DB)
    db_path.parent.mkdir(parents=True, exist_ok=True)
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
            workspace = _upsert_project_workspace(store=store, title=title, workspace_path=_invocation_dir())
            print(f"Added project `{workspace.workspace_id}` at {workspace.path}.")
            return workspace.workspace_id
        print("No workspaces found. Temporary mode selected.")
        return None

    print("Available workspaces:")
    for index, workspace in enumerate(workspaces, start=1):
        print(f"{index}. {workspace.workspace_id} ({workspace.backend}/{workspace.transport})")
    print(f"{len(workspaces) + 1}. add-project (bind current directory as a new project)")

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
            workspace = _upsert_project_workspace(store=store, title=title, workspace_path=_invocation_dir())
            print(f"Added project `{workspace.workspace_id}` at {workspace.path}.")
            return workspace.workspace_id
        if resolved is None:
            print("Temporary mode selected. Use /use <workspace> later to move into a formal project.")
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
    if project:
        response = adapter.handle_message(_local_message(chat_id=chat_id, user_id=user_id, text=f"/use {project}"))
        print(response.text)
    elif sys.stdin.isatty():
        print(adapter.handle_message(_local_message(chat_id=chat_id, user_id=user_id, text="/projects")).text)
        selected_workspace = _pick_startup_workspace(store=store)
        if selected_workspace:
            response = adapter.handle_message(_local_message(chat_id=chat_id, user_id=user_id, text=f"/use {selected_workspace}"))
            print(response.text)
        print("Type /help to see unified project commands.")

    while True:
        try:
            line = input("> ")
        except EOFError:
            print()
            return 0
        text = line.strip()
        if not text:
            continue
        if text in {"/quit", "/exit"}:
            return 0
        response = adapter.handle_message(_local_message(chat_id=chat_id, user_id=user_id, text=text))
        print(response.text)


def _provider_command(provider: str) -> str:
    normalized = provider.strip().lower()
    env_key = f"ASH_{normalized.upper()}_BIN"
    explicit = os.getenv(env_key, "").strip()
    if explicit:
        return explicit
    if normalized == "claude":
        return str(Path.home() / ".local/bin/claude")
    if normalized == "codex":
        return str(Path.home() / ".local/bin/codex")
    return normalized


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
    if bound_session_id and _provider_session_matches_workspace(
        provider=provider,
        session_id=bound_session_id,
        workspace_path=workspace_path,
    ):
        return bound_session_id

    db_path = Path(os.getenv("ASH_PROJECT_SESSION_DB", "").strip() or DEFAULT_PROJECT_SESSION_DB)
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
        parts.append(f"context={snapshot['recent_context']}")
    elif snapshot.get("memory"):
        parts.append(f"memory={snapshot['memory']}")
    hints = [item for item in snapshot.get("recent_hints", []) if item]
    if hints:
        parts.append(f"hints={'; '.join(hints[:2])}")
    return " | ".join(parts)


def _provider_launch_argv(*, provider: str, command: str, session_id: str | None) -> list[str]:
    if session_id:
        if provider == "codex":
            return [command, "resume", session_id]
        if provider == "claude":
            return [command, "--resume", session_id]
        return [command]
    return [command]


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
    env["ASH_PROJECT_MEMORY_RECENT_CONTEXT"] = snapshot["recent_context"]
    env["ASH_PROJECT_MEMORY_SUMMARY"] = snapshot["memory"]
    env["ASH_PROJECT_MEMORY_HINTS"] = " || ".join(snapshot["recent_hints"])
    return True


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
    if workspace is not None:
        context_store = ProjectContextStore()
        env["ASH_ACTIVE_WORKSPACE"] = workspace.workspace_id
        env["ASH_PROJECT_PATH"] = work_dir
        env["CCB_WORK_DIR"] = work_dir
        env["CCB_RUN_DIR"] = work_dir
        print(f"[agent-swarm-hub] entering native {provider} CLI")
        print(f"[agent-swarm-hub] workspace={workspace.workspace_id}")
        print(f"[agent-swarm-hub] path={work_dir}")
        snapshot = context_store.build_memory_snapshot(workspace.path)
        if _inject_project_memory_env(env, workspace_path=workspace.path, context_store=context_store, snapshot=snapshot):
            print("[agent-swarm-hub] project_memory=compact-loaded")
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
            print(f"[agent-swarm-hub] resume_session={resume_session_id}")
        else:
            print("[agent-swarm-hub] session_mode=fresh-project-context")
            memory_summary = _build_memory_summary(snapshot=snapshot)
            if memory_summary:
                print(f"[agent-swarm-hub] memory_summary={memory_summary}")
    else:
        resume_session_id = None
        print(f"[agent-swarm-hub] entering native {provider} CLI in temporary mode")
        print(f"[agent-swarm-hub] path={work_dir}")

    os.chdir(work_dir)
    command = _provider_command(provider)
    os.execvpe(
        command,
        _provider_launch_argv(
            provider=provider,
            command=command,
            session_id=resume_session_id,
        ),
        env,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="agent-swarm-hub local runners")
    parser.add_argument(
        "--env-file",
        default=".env.local",
        help="Optional local env file to load before reading config",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

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

    args = parser.parse_args()
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

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
