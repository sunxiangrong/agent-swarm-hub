from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from .adapter import CCConnectAdapter
from . import cli_ops
from . import local_chat
from . import native_entry
from . import workspace_ops
from .config import RuntimeConfig, apply_runtime_env, load_env_file
from .dashboard import serve_dashboard
from .openviking_support import (
    build_openviking_config_from_env,
    import_project_tree_to_openviking,
    openviking_server_url,
    read_openviking_config,
    read_openviking_overview,
    resolve_openviking_config_path,
    sync_project_tree_to_openviking,
    validate_openviking_config,
    write_openviking_config,
)
from .project_context import ProjectContextStore
from .remote import RemoteMessage
from .session_store import SessionStore, WorkspaceRecord
from .telegram_polling import TelegramPollingRunner
from .telegram_service import TelegramService

ADD_PROJECT_SENTINEL = workspace_ops.ADD_PROJECT_SENTINEL
LarkWebSocketRunner = None


def _local_message(*, chat_id: str, user_id: str, text: str) -> RemoteMessage:
    return local_chat.local_message(chat_id=chat_id, user_id=user_id, text=text)


def _resolve_workspace_selection(selection: str, workspaces: list[WorkspaceRecord]) -> str | None:
    return workspace_ops.resolve_workspace_selection(selection, workspaces)


def _shared_projects_as_workspaces() -> list[WorkspaceRecord]:
    return workspace_ops.shared_projects_as_workspaces()


def _pick_startup_workspace(*, store: SessionStore, require_path: bool = False) -> str | None:
    return workspace_ops.pick_startup_workspace(
        store=store,
        require_path=require_path,
        shared_projects_as_workspaces_cb=_shared_projects_as_workspaces,
        workspace_path_is_enterable_cb=workspace_ops.workspace_path_is_enterable,
        upsert_project_workspace_cb=workspace_ops.upsert_project_workspace,
        new_project_workspace_path_cb=workspace_ops.new_project_workspace_path,
        resolve_workspace_selection_cb=workspace_ops.resolve_workspace_selection,
    )


def _run_local_chat(*, provider: str, chat_id: str, user_id: str, project: str | None) -> int:
    return local_chat.run_local_chat(
        provider=provider,
        chat_id=chat_id,
        user_id=user_id,
        project=project,
        local_message_cb=_local_message,
        pick_startup_workspace_cb=_pick_startup_workspace,
        auto_prepare_openviking_project_cb=_auto_prepare_openviking_project,
        finalize_local_chat_memory_cb=_finalize_local_chat_memory,
        checkpoint_local_chat_memory_cb=_checkpoint_local_chat_memory,
    )


def _checkpoint_local_chat_memory(*, adapter: CCConnectAdapter, chat_id: str, user_id: str, session_key: str) -> None:
    local_chat.checkpoint_local_chat_memory(
        adapter=adapter,
        chat_id=chat_id,
        user_id=user_id,
        session_key=session_key,
        local_message_cb=_local_message,
        consolidate_bound_workspace_memory_cb=_consolidate_bound_workspace_memory,
    )


def _finalize_local_chat_memory(*, adapter: CCConnectAdapter, chat_id: str, user_id: str, session_key: str) -> None:
    local_chat.finalize_local_chat_memory(
        adapter=adapter,
        chat_id=chat_id,
        user_id=user_id,
        session_key=session_key,
        local_message_cb=_local_message,
        consolidate_bound_workspace_memory_cb=_consolidate_bound_workspace_memory,
    )


def _consolidate_bound_workspace_memory(*, adapter: CCConnectAdapter, session_key: str, workspace_id: str) -> None:
    local_chat.consolidate_bound_workspace_memory(
        adapter=adapter,
        session_key=session_key,
        workspace_id=workspace_id,
        sync_project_memory_artifacts_cb=_sync_project_memory_artifacts,
    )


def _resolve_workspace_record(*, store: SessionStore, workspace_id: str | None, provider: str) -> WorkspaceRecord | None:
    return native_entry.resolve_workspace_record(store=store, workspace_id=workspace_id, provider=provider)


def _latest_provider_session(*, project_id: str, provider: str, workspace_path: str | None, context_store: ProjectContextStore | None = None) -> str | None:
    return native_entry.latest_provider_session(
        project_id=project_id,
        provider=provider,
        workspace_path=workspace_path,
        context_store=context_store,
    )


def _provider_session_matches_workspace(*, provider: str, session_id: str, workspace_path: str | None) -> bool:
    return native_entry.provider_session_matches_workspace(
        provider=provider,
        session_id=session_id,
        workspace_path=workspace_path,
    )


def _provider_session_exists(*, provider: str, session_id: str) -> bool:
    return native_entry.provider_session_exists(provider=provider, session_id=session_id)


def _find_codex_session_file(session_id: str) -> Path | None:
    return native_entry.find_codex_session_file(session_id)


def _find_running_codex_session(*, session_id: str | None, work_dir: str | None) -> dict[str, str] | None:
    return native_entry.find_running_codex_session(session_id=session_id, work_dir=work_dir)


def _read_codex_session_cwd(session_file: Path) -> str:
    return native_entry.read_codex_session_cwd(session_file)


def _project_provider_sessions(*, project_id: str, workspace_path: str | None, context_store: ProjectContextStore | None = None) -> dict[str, str]:
    return native_entry.project_provider_sessions(
        project_id=project_id,
        workspace_path=workspace_path,
        context_store=context_store,
    )


def _build_memory_summary(*, snapshot: dict[str, str]) -> str:
    focus = snapshot.get("focus") or ""
    recent_context = snapshot.get("recent_context") or ""
    memory = snapshot.get("memory") or ""
    hints = [item for item in snapshot.get("recent_hints", []) if item]
    parts = []
    if focus:
        parts.append(f"focus={focus}")
    if recent_context:
        parts.append(f"current_state={recent_context}")
    elif memory:
        parts.append(f"memory={memory}")
    if hints:
        parts.append(f"hints={'; '.join(hints[:2])}")
    return " | ".join(parts)


def _project_summary_field(summary: str, prefix: str) -> str:
    return native_entry.project_summary_field(summary, prefix)


def _build_project_summary_prompt(*, workspace_id: str, work_dir: str, summary: str, snapshot: dict[str, str], driver_provider: str) -> str:
    return native_entry.build_project_summary_prompt(
        workspace_id=workspace_id,
        work_dir=work_dir,
        summary=summary,
        snapshot=snapshot,
        driver_provider=driver_provider,
        read_openviking_overview_cb=read_openviking_overview,
    )


def _print_project_entry_view(
    *,
    provider: str,
    workspace_id: str,
    work_dir: str,
    project_summary: str,
    snapshot: dict[str, str],
    resume_session_id: str | None,
) -> None:
    native_entry.print_project_entry_view(
        provider=provider,
        workspace_id=workspace_id,
        work_dir=work_dir,
        project_summary=project_summary,
        snapshot=snapshot,
        resume_session_id=resume_session_id,
    )


def _confirm_project_entry(*, provider: str) -> None:
    native_entry.confirm_project_entry(provider=provider)


def _provider_launch_argv(
    *,
    provider: str,
    command: str,
    session_id: str | None,
    work_dir: str,
    bootstrap_prompt: str = "",
) -> list[str]:
    return native_entry.provider_launch_argv(
        provider=provider,
        command=command,
        session_id=session_id,
        work_dir=work_dir,
        bootstrap_prompt=bootstrap_prompt,
    )


def _scripts_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "scripts"


def _prepend_path(env: dict[str, str], entry: str) -> None:
    current = env.get("PATH", "")
    if not current:
        env["PATH"] = entry
        return
    parts = current.split(os.pathsep)
    if entry not in parts:
        env["PATH"] = os.pathsep.join([entry, *parts])


def _clear_project_runtime_env(env: dict[str, str]) -> None:
    native_entry.clear_project_runtime_env(env)


def _inject_project_identity_env(
    env: dict[str, str],
    *,
    workspace_id: str | None,
    work_dir: str,
    provider: str,
    provider_session_id: str | None,
    session_mode: str,
) -> None:
    native_entry.inject_project_identity_env(
        env,
        workspace_id=workspace_id,
        work_dir=work_dir,
        provider=provider,
        provider_session_id=provider_session_id,
        session_mode=session_mode,
    )


def _inject_project_memory_env(
    env: dict[str, str],
    *,
    workspace_path: str | None,
    context_store: ProjectContextStore | None = None,
    snapshot: dict[str, str] | None = None,
) -> bool:
    return native_entry.inject_project_memory_env(
        env,
        workspace_path=workspace_path,
        context_store=context_store,
        snapshot=snapshot,
        read_openviking_overview_cb=read_openviking_overview,
    )


def _workspace_path_matches(workspace_path: str | None, candidate_cwd: str | None) -> bool:
    return native_entry.workspace_path_matches(workspace_path, candidate_cwd)


def _extract_codex_history(session_id: str) -> list[str]:
    return native_entry.extract_codex_history(session_id)


def _extract_claude_text(content) -> str:
    return native_entry.extract_claude_text(content)


def _collect_codex_workspace_sessions(workspace_path: str | None) -> dict[str, dict[str, object]]:
    return native_entry.collect_codex_workspace_sessions(workspace_path)


def _collect_claude_workspace_sessions(workspace_path: str | None) -> dict[str, dict[str, object]]:
    return native_entry.collect_claude_workspace_sessions(workspace_path)


def _collect_workspace_provider_sessions(provider: str, workspace_path: str | None) -> dict[str, dict[str, object]]:
    return native_entry.collect_workspace_provider_sessions(provider, workspace_path)


def _select_postrun_session(
    *,
    provider: str,
    workspace_path: str | None,
    before: dict[str, dict[str, object]],
    preferred_session_id: str | None,
) -> dict[str, object] | None:
    return native_entry.select_postrun_session(
        provider=provider,
        workspace_path=workspace_path,
        before=before,
        preferred_session_id=preferred_session_id,
    )


def _record_provider_binding_and_memory(
    *,
    context_store: ProjectContextStore,
    project_id: str,
    provider: str,
    workspace_path: str,
    session_meta: dict[str, object] | None,
    fallback_snapshot: dict[str, str],
) -> None:
    native_entry.record_provider_binding_and_memory(
        context_store=context_store,
        project_id=project_id,
        provider=provider,
        workspace_path=workspace_path,
        session_meta=session_meta,
        fallback_snapshot=fallback_snapshot,
        consolidate_project_memory_artifacts_cb=_consolidate_project_memory_artifacts,
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
    native_entry.sync_native_workspace_runtime(
        session_store=session_store,
        context_store=context_store,
        project_id=project_id,
        provider=provider,
        session_meta=session_meta,
        fallback_snapshot=fallback_snapshot,
    )


def _backfill_workspace_provider_sessions(
    *,
    context_store: ProjectContextStore,
    project_id: str,
    workspace_path: str,
    fallback_snapshot: dict[str, str],
) -> dict[str, str]:
    return native_entry.backfill_workspace_provider_sessions(
        context_store=context_store,
        project_id=project_id,
        workspace_path=workspace_path,
        fallback_snapshot=fallback_snapshot,
        consolidate_project_memory_artifacts_cb=_consolidate_project_memory_artifacts,
    )


def _extract_project_memory_from_messages(
    messages: list[str],
    *,
    fallback_snapshot: dict[str, str],
) -> dict[str, object]:
    return native_entry.extract_project_memory_from_messages(
        messages,
        fallback_snapshot=fallback_snapshot,
    )


def _is_meaningful_project_memory_message(text: str) -> bool:
    return native_entry.is_meaningful_project_memory_message(text)


def _is_meta_project_memory_message(text: str) -> bool:
    return native_entry.is_meta_project_memory_message(text)


def _project_sessions_current(project_id: str) -> int:
    return cli_ops.project_sessions_current(project_id)


def _project_sessions_list(project_id: str, provider: str | None) -> int:
    return cli_ops.project_sessions_list(project_id, provider)


def _project_sessions_use(project_id: str, provider: str, session_id: str) -> int:
    return cli_ops.project_sessions_use(
        project_id,
        provider,
        session_id,
        sync_project_memory_artifacts_cb=_sync_project_memory_artifacts,
    )


def _sync_project_memory_artifacts(store: ProjectContextStore, project_id: str) -> None:
    cli_ops.sync_project_memory_artifacts(
        store,
        project_id,
        sync_openviking_project_artifacts_cb=_sync_openviking_project_artifacts,
    )


def _openviking_auto_manage_enabled() -> bool:
    return cli_ops.openviking_auto_manage_enabled()


def _openviking_health_ok(config_path: Path) -> bool:
    return cli_ops.openviking_health_ok(config_path)


def _ensure_openviking_service_running(*, config_out: str | None = None) -> bool:
    if not _openviking_auto_manage_enabled():
        return False
    config_path = resolve_openviking_config_path(config_out)
    if config_path is None:
        try:
            config_path = _ensure_openviking_config(config_out=config_out)
        except Exception:
            return False
    if _openviking_health_ok(config_path):
        return True
    env = os.environ.copy()
    env["OPENVIKING_CONFIG_FILE"] = str(config_path)
    try:
        subprocess.Popen(
            ["openviking-server"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return False
    time.sleep(0.6)
    return _openviking_health_ok(config_path)


def _push_openviking_project_live(project_id: str, *, rebuild_tree: bool = False) -> bool:
    if not project_id or not _openviking_auto_manage_enabled():
        return False
    if not _ensure_openviking_service_running():
        return False
    try:
        if rebuild_tree:
            return import_project_tree_to_openviking(project_id)
        return sync_project_tree_to_openviking(project_id)
    except Exception:
        return False


def _auto_prepare_openviking_project(project_id: str | None) -> None:
    cli_ops.auto_prepare_openviking_project(
        project_id,
        push_openviking_project_live_cb=_push_openviking_project_live,
    )


def _sync_openviking_project_artifacts(project_id: str, *, rebuild_tree: bool = False) -> None:
    cli_ops.sync_openviking_project_artifacts(
        project_id,
        rebuild_tree=rebuild_tree,
        push_openviking_project_live_cb=_push_openviking_project_live,
    )


def _consolidate_project_memory_artifacts(
    store: ProjectContextStore,
    project_id: str,
    *,
    live_summary: str = "",
    recent_messages: list[str] | None = None,
) -> bool:
    return cli_ops.consolidate_project_memory_artifacts(
        store,
        project_id,
        live_summary=live_summary,
        recent_messages=recent_messages,
        sync_project_memory_artifacts_cb=_sync_project_memory_artifacts,
    )


def _project_sessions_sync_memory(project_id: str | None, *, sync_all: bool) -> int:
    return cli_ops.project_sessions_sync_memory(
        project_id,
        sync_all=sync_all,
        consolidate_project_memory_artifacts_cb=_consolidate_project_memory_artifacts,
    )


def _project_sessions_remove_project(project_id: str) -> int:
    return cli_ops.project_sessions_remove_project(project_id)


def _project_sessions_cleanup_runtime(
    *,
    apply: bool,
    tmux_grace_minutes: int,
    stale_workspace_days: int,
    pane_log_days: int,
    ccb_registry_days: int,
    prune_openviking_imports: bool,
    openviking_import_days: int,
) -> int:
    return cli_ops.project_sessions_cleanup_runtime(
        apply=apply,
        tmux_grace_minutes=tmux_grace_minutes,
        stale_workspace_days=stale_workspace_days,
        pane_log_days=pane_log_days,
        ccb_registry_days=ccb_registry_days,
        prune_openviking_imports=prune_openviking_imports,
        openviking_import_days=openviking_import_days,
    )


def _run_local_native(*, provider: str, project: str | None) -> int:
    # native_entry.py owns the heavy native-provider launch workflow. cli.py
    # keeps this wrapper so tests and shell entrypoints retain a stable import.
    return native_entry.run_local_native(
        provider=provider,
        project=project,
        pick_startup_workspace_cb=_pick_startup_workspace,
        workspace_path_is_enterable_cb=workspace_ops.workspace_path_is_enterable,
        auto_prepare_openviking_project_cb=_auto_prepare_openviking_project,
        consolidate_project_memory_artifacts_cb=_consolidate_project_memory_artifacts,
        read_openviking_overview_cb=read_openviking_overview,
    )


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
    print("  project-sessions cleanup-runtime  clean stale runtime artifacts")
    print("  lark-ws                        start Lark websocket listener")
    print("  telegram-poll                  run Telegram polling loop")
    print("")
    print("Examples:")
    print("  agent-swarm-hub chat codex agent-browser")
    print("  agent-swarm-hub swarm claude")
    print("  agent-swarm-hub dash")


def _lark_ws_runner_cls():
    global LarkWebSocketRunner
    if LarkWebSocketRunner is None:
        from .lark_ws_runner import LarkWebSocketRunner as _Runner

        LarkWebSocketRunner = _Runner
    return LarkWebSocketRunner


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


def _default_openviking_config_path() -> Path:
    return cli_ops._default_openviking_config_path()


def _ensure_openviking_config(*, config_out: str | None) -> Path:
    config_path = Path(config_out).expanduser().resolve() if config_out else _default_openviking_config_path()
    has_openviking_env = any(
        os.environ.get(name)
        for name in (
            "OPENVIKING_ARK_API_KEY",
            "OPENVIKING_VLM_API_KEY",
            "OPENVIKING_EMBEDDING_API_KEY",
            "OPENVIKING_VLM_MODEL",
            "OPENVIKING_EMBEDDING_MODEL",
            "OPENVIKING_STORAGE_WORKSPACE",
        )
    )
    # Keep the config resolution logic in cli.py so tests and higher-level
    # wrappers can monkeypatch the OpenViking helpers without reaching into
    # the split implementation module.
    if has_openviking_env or not config_path.exists():
        config = build_openviking_config_from_env()
        validate_openviking_config(config)
        write_openviking_config(config, config_path)
    else:
        validate_openviking_config(read_openviking_config(config_path))
    return config_path


def _run_openviking_server(*, config_out: str | None, write_only: bool) -> int:
    config_path = _ensure_openviking_config(config_out=config_out)
    print(config_path)
    if write_only:
        return 0
    env = os.environ.copy()
    env["OPENVIKING_CONFIG_FILE"] = str(config_path)
    return subprocess.run(["openviking-server"], env=env, check=False).returncode


def _openviking_status(*, config_out: str | None) -> int:
    import urllib.error
    import urllib.request

    config_path = _ensure_openviking_config(config_out=config_out)
    config = read_openviking_config(config_path)
    url = openviking_server_url(config)
    print(f"Config: {config_path}")
    print(f"Server: {url}")
    try:
        with urllib.request.urlopen(f"{url}/api/v1/health", timeout=2.0) as response:
            body = response.read().decode("utf-8", errors="ignore").strip()
        print(f"Health: ok {body or ''}".strip())
        return 0
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"Health: unreachable ({exc})")
        return 1


def _openviking_sync(*, project: str | None, sync_all: bool, push_live: bool, rebuild_tree: bool) -> int:
    return cli_ops.openviking_sync(
        project=project,
        sync_all=sync_all,
        push_live=push_live,
        rebuild_tree=rebuild_tree,
    )


def _openviking_tui(*, project: str | None) -> int:
    return cli_ops.openviking_tui(project=project)


def _handle_realtime_command(args, *, config: RuntimeConfig) -> int | None:
    if args.command == "lark-ws":
        if args.print_config:
            print(
                {
                    "enabled": config.lark.enabled,
                    "app_id": config.lark.app_id,
                    "verify_token": config.lark.verify_token,
                    "encrypt_key_configured": bool(config.lark.encrypt_key),
                }
            )
            return 0

        runner = _lark_ws_runner_cls().create(config.lark)
        runner.run_forever()
        return 0
    if args.command == "telegram-poll":
        if args.print_config:
            print(
                {
                    "enabled": config.telegram.enabled,
                    "bot_token_configured": bool(config.telegram.bot_token),
                    "polling_timeout_s": config.telegram.polling_timeout_s,
                    "parse_mode": config.telegram.default_parse_mode or None,
                }
            )
            return 0

        polling = TelegramPollingRunner(TelegramService(config.telegram))
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
    return None


def _handle_local_entry_command(args, *, config: RuntimeConfig) -> int | None:
    if args.command == "local-chat":
        provider = (args.provider or config.executor_mode or "codex").strip().lower()
        return _run_local_chat(
            provider=provider,
            chat_id=args.chat_id,
            user_id=args.user_id,
            project=args.project,
        )
    if args.command == "local-native":
        provider = (args.provider or config.executor_mode or "codex").strip().lower()
        return _run_local_native(provider=provider, project=args.project)
    if args.command == "chat":
        provider, project = _resolve_short_provider_project(arg1=args.provider, arg2=args.project, config=config)
        return _run_local_native(provider=provider, project=project)
    if args.command == "swarm":
        provider, project = _resolve_short_provider_project(arg1=args.provider, arg2=args.project, config=config)
        return _run_local_chat(
            provider=provider,
            chat_id="local-cli",
            user_id="local-user",
            project=project,
        )
    return None


def _handle_openviking_command(args) -> int | None:
    if args.command not in {"openviking", "ov"}:
        return None
    if args.action == "start":
        return _run_openviking_server(config_out=args.config_out, write_only=args.write_only)
    if args.action == "status":
        return _openviking_status(config_out=args.config_out)
    if args.action == "sync":
        return _openviking_sync(project=args.project, sync_all=args.all, push_live=args.push_live, rebuild_tree=args.rebuild_tree)
    if args.action == "tui":
        return _openviking_tui(project=args.project)
    return 1


def _handle_project_sessions_command(args) -> int | None:
    if args.command != "project-sessions":
        return None
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
    if args.project_sessions_command == "cleanup-runtime":
        return _project_sessions_cleanup_runtime(
            apply=args.apply,
            tmux_grace_minutes=args.tmux_grace_minutes,
            stale_workspace_days=args.stale_workspace_days,
            pane_log_days=args.pane_log_days,
            ccb_registry_days=args.ccb_registry_days,
            prune_openviking_imports=args.prune_openviking_imports,
            openviking_import_days=args.openviking_import_days,
        )
    return 1


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
    openviking = subparsers.add_parser("openviking", help="Manage OpenViking config, server, sync, and TUI")
    openviking.add_argument("action", nargs="?", choices=("start", "status", "sync", "tui"), default="start")
    openviking.add_argument("project", nargs="?", default=None, help="Optional project id for sync/tui")
    openviking.add_argument("--config-out", default=None, help="Optional path for generated ov.conf")
    openviking.add_argument("--write-only", action="store_true", help="Only write ov.conf and exit")
    openviking.add_argument("--push-live", action="store_true", help="When syncing, also push the project tree into live OV resources")
    openviking.add_argument("--all", action="store_true", help="When syncing, process all projects")
    openviking.add_argument("--rebuild-tree", action="store_true", help="When syncing, rebuild the whole project tree instead of updating current files in place")
    ov_short = subparsers.add_parser("ov", help="Shortcut for openviking")
    ov_short.add_argument("action", nargs="?", choices=("start", "status", "sync", "tui"), default="start")
    ov_short.add_argument("project", nargs="?", default=None, help="Optional project id for sync/tui")
    ov_short.add_argument("--config-out", default=None, help="Optional path for generated ov.conf")
    ov_short.add_argument("--write-only", action="store_true", help="Only write ov.conf and exit")
    ov_short.add_argument("--push-live", action="store_true", help="When syncing, also push the project tree into live OV resources")
    ov_short.add_argument("--all", action="store_true", help="When syncing, process all projects")
    ov_short.add_argument("--rebuild-tree", action="store_true", help="When syncing, rebuild the whole project tree instead of updating current files in place")
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
    project_sessions_cleanup = project_sessions_sub.add_parser(
        "cleanup-runtime",
        help="Clean stale runtime artifacts (tmux sessions, logs, stale workspace records)",
    )
    project_sessions_cleanup.add_argument(
        "--apply",
        action="store_true",
        help="Apply cleanup actions. Without this flag, only print a dry-run plan.",
    )
    project_sessions_cleanup.add_argument("--tmux-grace-minutes", type=int, default=30)
    project_sessions_cleanup.add_argument("--stale-workspace-days", type=int, default=7)
    project_sessions_cleanup.add_argument("--pane-log-days", type=int, default=7)
    project_sessions_cleanup.add_argument("--ccb-registry-days", type=int, default=7)
    project_sessions_cleanup.add_argument(
        "--prune-openviking-imports",
        action="store_true",
        help="Also prune stale OpenViking import project directories not mapped to local projects.",
    )
    project_sessions_cleanup.add_argument("--openviking-import-days", type=int, default=14)

    args = parser.parse_args()
    if not args.command:
        _print_main_menu()
        return 0
    load_env_file(args.env_file)
    apply_runtime_env()
    config = RuntimeConfig.from_env()

    result = _handle_realtime_command(args, config=config)
    if result is not None:
        return result
    result = _handle_local_entry_command(args, config=config)
    if result is not None:
        return result
    if args.command in {"dashboard", "dash"}:
        if args.open:
            _open_dashboard_url(host=args.host, port=args.port)
        serve_dashboard(host=args.host, port=args.port)
        return 0
    result = _handle_openviking_command(args)
    if result is not None:
        return result
    result = _handle_project_sessions_command(args)
    if result is not None:
        return result

    return 1


if __name__ == "__main__":
    raise SystemExit(main())


def ash_chat_main() -> int:
    return _run_short_entry(mode="chat")


def ash_swarm_main() -> int:
    return _run_short_entry(mode="swarm")
