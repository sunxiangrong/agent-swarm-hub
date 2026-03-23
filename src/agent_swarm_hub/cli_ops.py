from __future__ import annotations
"""Operational command handlers used by the top-level CLI router.

This module owns side-effect-heavy commands such as OpenViking management,
project session maintenance, and runtime cleanup. cli.py keeps only the stable
entrypoints and thin compatibility wrappers.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

from .openviking_support import (
    build_openviking_config_from_env,
    import_project_tree_to_openviking,
    openviking_server_url,
    read_openviking_config,
    resolve_openviking_config_path,
    sync_project_tree_to_openviking,
    validate_openviking_config,
    write_openviking_config,
)
from .project_context import ProjectContextStore, project_ov_resource_uri
from .runtime_cleanup import run_runtime_cleanup
from .session_store import SessionStore

def project_sessions_current(project_id: str) -> int:
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


def project_sessions_list(project_id: str, provider: str | None) -> int:
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


def sync_project_memory_artifacts(
    store: ProjectContextStore,
    project_id: str,
    *,
    sync_openviking_project_artifacts_cb,
) -> None:
    # Keep the exported project files and OV artifacts aligned after any
    # project-session mutation.
    store.sync_project_summary(project_id)
    sync_openviking_project_artifacts_cb(project_id)
    store.sync_project_memory_file(project_id)
    store.sync_project_skill_file(project_id)
    store.sync_global_memory_file()


def project_sessions_use(
    project_id: str,
    provider: str,
    session_id: str,
    *,
    sync_project_memory_artifacts_cb,
) -> int:
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
    sync_project_memory_artifacts_cb(store, project_id)
    print(f"Current {provider} session for `{project_id}` set to {session_id}.")
    return 0


def consolidate_project_memory_artifacts(
    store: ProjectContextStore,
    project_id: str,
    *,
    live_summary: str = "",
    recent_messages: list[str] | None = None,
    sync_project_memory_artifacts_cb,
) -> bool:
    ok = store.consolidate_project_memory(
        project_id,
        live_summary=live_summary,
        recent_messages=recent_messages,
    )
    sync_project_memory_artifacts_cb(store, project_id)
    return ok


def project_sessions_sync_memory(
    project_id: str | None,
    *,
    sync_all: bool,
    consolidate_project_memory_artifacts_cb,
) -> int:
    store = ProjectContextStore()
    if sync_all:
        projects = store.list_projects()
        if not projects:
            print("No projects recorded.")
            return 0
        for project in projects:
            consolidate_project_memory_artifacts_cb(store, project.project_id)
            print(f"Synced project memory for `{project.project_id}`.")
        return 0
    if not project_id:
        print("Provide a project id or use --all.", file=sys.stderr)
        return 2
    project = store.get_project(project_id)
    if project is None:
        print(f"Unknown project: {project_id}", file=sys.stderr)
        return 2
    consolidate_project_memory_artifacts_cb(store, project_id)
    print(f"Synced project memory for `{project_id}`.")
    return 0


def project_sessions_remove_project(project_id: str) -> int:
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


def project_sessions_cleanup_runtime(
    *,
    apply: bool,
    tmux_grace_minutes: int,
    stale_workspace_days: int,
    pane_log_days: int,
    ccb_registry_days: int,
    prune_openviking_imports: bool,
    openviking_import_days: int,
) -> int:
    report = run_runtime_cleanup(
        apply=apply,
        tmux_grace_minutes=tmux_grace_minutes,
        stale_workspace_days=stale_workspace_days,
        pane_log_days=pane_log_days,
        ccb_registry_days=ccb_registry_days,
        prune_openviking_imports=prune_openviking_imports,
        openviking_import_days=openviking_import_days,
    )
    print(f"Runtime cleanup mode: {report['mode']}")
    print(f"Total actions: {report['total']} | applied: {report['applied']} | errors: {report['errors']}")
    by_kind = report.get("by_kind") or {}
    if by_kind:
        print("By kind:")
        for kind in sorted(by_kind):
            print(f"  - {kind}: {by_kind[kind]}")
    actions = report.get("actions") or []
    if actions:
        print("Actions:")
        for item in actions:
            status = "applied" if item.get("applied") else ("error" if item.get("error") else "planned")
            reason = str(item.get("reason") or "")
            if item.get("error"):
                reason = f"{reason} | error={item['error']}"
            print(f"  - [{status}] {item.get('kind')}: {item.get('target')} ({reason})")
    else:
        print("No stale runtime artifacts detected.")
    return 1 if int(report.get("errors") or 0) > 0 else 0


def openviking_auto_manage_enabled() -> bool:
    explicit = os.getenv("ASH_OPENVIKING_AUTO")
    if explicit is not None:
        return explicit.strip().lower() not in {"0", "false", "no", "off"}
    # Local macOS defaults to disabled; server-like runtimes stay enabled.
    return sys.platform != "darwin"


def ensure_openviking_config(*, config_out: str | None) -> Path:
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
    if has_openviking_env or not config_path.exists():
        config = build_openviking_config_from_env()
        validate_openviking_config(config)
        write_openviking_config(config, config_path)
    else:
        validate_openviking_config(read_openviking_config(config_path))
    return config_path


def openviking_health_ok(config_path: Path) -> bool:
    import urllib.error
    import urllib.request

    try:
        config = read_openviking_config(config_path)
        with urllib.request.urlopen(f"{openviking_server_url(config)}/api/v1/health", timeout=1.0):
            return True
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError):
        return False


def ensure_openviking_service_running(*, config_out: str | None = None, ensure_openviking_config_cb=None) -> bool:
    if not openviking_auto_manage_enabled():
        return False
    config_path = resolve_openviking_config_path(config_out)
    if config_path is None:
        try:
            config_path = (ensure_openviking_config_cb or ensure_openviking_config)(config_out=config_out)
        except Exception:
            return False
    if openviking_health_ok(config_path):
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
    return openviking_health_ok(config_path)


def push_openviking_project_live(
    project_id: str,
    *,
    rebuild_tree: bool = False,
    ensure_openviking_service_running_cb=None,
) -> bool:
    if not project_id or not openviking_auto_manage_enabled():
        return False
    if not (ensure_openviking_service_running_cb or ensure_openviking_service_running)():
        return False
    try:
        if rebuild_tree:
            return import_project_tree_to_openviking(project_id)
        return sync_project_tree_to_openviking(project_id)
    except Exception:
        return False


def auto_prepare_openviking_project(
    project_id: str | None,
    *,
    push_openviking_project_live_cb=None,
) -> None:
    if not project_id:
        return
    (push_openviking_project_live_cb or push_openviking_project_live)(project_id)


def sync_openviking_project_artifacts(
    project_id: str,
    *,
    rebuild_tree: bool = False,
    push_openviking_project_live_cb=None,
) -> None:
    # Rebuild local OV export artifacts first, then optionally push them into a
    # live OV service when automatic synchronization is enabled.
    repo_root = Path(__file__).resolve().parents[2]
    scripts_dir = repo_root / "scripts"
    commands = [
        [
            sys.executable,
            str(scripts_dir / "build-openviking-project-tree.py"),
            "--project",
            project_id,
            *(["--rebuild"] if rebuild_tree else []),
        ],
        [sys.executable, str(scripts_dir / "build-openviking-memory-bundle.py"), "--project", project_id],
        [sys.executable, str(scripts_dir / "build-openviking-project-brain.py"), "--project", project_id],
    ]
    for argv in commands:
        try:
            subprocess.run(argv, cwd=str(repo_root), check=False)
        except OSError:
            return
    (push_openviking_project_live_cb or push_openviking_project_live)(project_id, rebuild_tree=rebuild_tree)


def run_openviking_server(*, config_out: str | None, write_only: bool, ensure_openviking_config_cb=None) -> int:
    config_path = (ensure_openviking_config_cb or ensure_openviking_config)(config_out=config_out)
    print(config_path)
    if write_only:
        return 0
    env = os.environ.copy()
    env["OPENVIKING_CONFIG_FILE"] = str(config_path)
    return subprocess.run(["openviking-server"], env=env, check=False).returncode


def openviking_status(*, config_out: str | None, ensure_openviking_config_cb=None) -> int:
    import urllib.error
    import urllib.request

    config_path = (ensure_openviking_config_cb or ensure_openviking_config)(config_out=config_out)
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


def openviking_sync(*, project: str | None, sync_all: bool, push_live: bool, rebuild_tree: bool) -> int:
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    argv = [sys.executable, str(scripts_dir / "sync-openviking-projects.py")]
    if project and not sync_all:
        argv.extend(["--project", project])
    if push_live:
        argv.append("--push-live")
    if rebuild_tree:
        argv.append("--rebuild-tree")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1]) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    env["NO_PROXY"] = "*"
    env["no_proxy"] = "*"
    return subprocess.run(argv, env=env, check=False).returncode


def openviking_tui(*, project: str | None) -> int:
    target = project_ov_resource_uri(project) if project else "viking://resources/projects"
    return subprocess.run(["ov", "tui", target], check=False).returncode


def _default_openviking_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "var" / "openviking" / "ov.conf"
