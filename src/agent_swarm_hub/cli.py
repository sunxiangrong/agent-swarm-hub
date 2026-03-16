from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .adapter import CCConnectAdapter
from .config import RuntimeConfig, apply_runtime_env, load_env_file
from .executor import build_executor_for_config
from .lark_ws_runner import LarkWebSocketRunner
from .project_context import ProjectContextStore
from .remote import RemoteMessage, RemotePlatform
from .session_store import SessionStore, WorkspaceRecord
from .telegram_polling import TelegramPollingRunner
from .telegram_service import TelegramService


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
    if raw.lower() in {"temporary", "temp"}:
        return None
    if raw.isdigit():
        index = int(raw) - 1
        if 0 <= index < len(workspaces):
            return workspaces[index].workspace_id
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


def _pick_startup_workspace(*, store: SessionStore) -> str | None:
    workspaces = _shared_projects_as_workspaces() or store.list_workspaces()
    if not workspaces:
        print("No workspaces found. Temporary mode selected.")
        return None

    print("Available workspaces:")
    for index, workspace in enumerate(workspaces, start=1):
        print(f"{index}. {workspace.workspace_id} ({workspace.backend}/{workspace.transport})")

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


def _run_local_native(*, provider: str, project: str | None) -> int:
    store = SessionStore()
    selected_workspace = project
    if not selected_workspace and sys.stdin.isatty():
        print("Select a project before entering the native agent CLI.")
        selected_workspace = _pick_startup_workspace(store=store)
    workspace = store.get_workspace(selected_workspace) if selected_workspace else None
    if selected_workspace and workspace is None:
        print(f"Unknown workspace: {selected_workspace}", file=sys.stderr)
        return 2

    work_dir = workspace.path if workspace and workspace.path else os.getcwd()
    env = os.environ.copy()
    if workspace is not None:
        env["ASH_ACTIVE_WORKSPACE"] = workspace.workspace_id
        env["CCB_WORK_DIR"] = work_dir
        env["CCB_RUN_DIR"] = work_dir
        print(f"[agent-swarm-hub] entering native {provider} CLI")
        print(f"[agent-swarm-hub] workspace={workspace.workspace_id}")
        print(f"[agent-swarm-hub] path={work_dir}")
    else:
        print(f"[agent-swarm-hub] entering native {provider} CLI in temporary mode")
        print(f"[agent-swarm-hub] path={work_dir}")

    os.chdir(work_dir)
    command = _provider_command(provider)
    os.execvpe(command, [command], env)
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
