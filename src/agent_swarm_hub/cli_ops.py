from __future__ import annotations
"""Operational command handlers used by the top-level CLI router.

This module owns side-effect-heavy commands such as OpenViking management,
project session maintenance, and runtime cleanup. cli.py keeps only the stable
entrypoints and thin compatibility wrappers.
"""

import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from .auto_continue import project_sessions_auto_continue
from .bridge_policy import (
    bridge_policy_env,
    init_bridge_policy,
    load_bridge_policy,
    render_bridge_env_exports,
    render_bridge_policy_summary,
    update_bridge_policy,
)
from .live_followup import LiveFollowupError, send_followup_to_live_session
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
from .runtime_monitor import run_runtime_monitor
from .runtime_health import (
    codex_process_health,
    find_running_codex_session,
    list_running_codex_processes,
    terminate_process,
)
from .runtime_cleanup import run_runtime_cleanup
from .session_store import SessionStore
from .swarm_launch import ensure_orchestrator_pane

_LOGIN_SHELL = os.getenv("SHELL") or "/bin/zsh"


def project_sessions_bridge_policy(
    project_id: str,
    *,
    init: bool,
    force: bool,
    set_ssh_targets: list[str] | None,
) -> int:
    store = ProjectContextStore()
    project = store.get_project(project_id)
    if project is None:
        print(f"Unknown project: {project_id}", file=sys.stderr)
        return 2
    workspace_path = (project.workspace_path or "").strip()
    if not workspace_path:
        print(f"Project `{project_id}` has no workspace path.", file=sys.stderr)
        return 2
    from .bridge_policy import bridge_policy_path

    policy_path = bridge_policy_path(workspace_path)
    existed_before = policy_path.exists()
    if init:
        policy_path = init_bridge_policy(project_id, workspace_path, force=force)
    if set_ssh_targets is not None:
        policy = update_bridge_policy(project_id, workspace_path, ssh_targets=set_ssh_targets)
        policy_path = bridge_policy_path(workspace_path)
        print(render_bridge_policy_summary(policy, path=policy_path))
        print("Updated SSH targets.")
        return 0
    policy = load_bridge_policy(project_id, workspace_path)
    print(render_bridge_policy_summary(policy, path=policy_path))
    if init:
        print("Initialized bridge policy file." if force or not existed_before else "Bridge policy file already exists.")
    return 0


def project_sessions_bridge_env(project_id: str, *, init: bool) -> int:
    store = ProjectContextStore()
    project = store.get_project(project_id)
    if project is None:
        print(f"Unknown project: {project_id}", file=sys.stderr)
        return 2
    workspace_path = (project.workspace_path or "").strip()
    if not workspace_path:
        print(f"Project `{project_id}` has no workspace path.", file=sys.stderr)
        return 2
    if init:
        init_bridge_policy(project_id, workspace_path)
    policy = load_bridge_policy(project_id, workspace_path)
    print(render_bridge_env_exports(policy))
    return 0


def project_sessions_bridge_status(
    project_id: str,
    *,
    provider: str | None,
    init: bool,
    exports: bool,
) -> int:
    store = ProjectContextStore()
    project = store.get_project(project_id)
    if project is None:
        print(f"Unknown project: {project_id}", file=sys.stderr)
        return 2
    workspace_path = (project.workspace_path or "").strip()
    if not workspace_path:
        print(f"Project `{project_id}` has no workspace path.", file=sys.stderr)
        return 2
    if init:
        init_bridge_policy(project_id, workspace_path)
    policy = load_bridge_policy(project_id, workspace_path)
    selected_provider = (provider or os.getenv("ASH_EXECUTOR") or "codex").strip().lower() or "codex"
    launch = ensure_orchestrator_pane(
        project_id=project_id,
        workspace_path=workspace_path,
        provider=selected_provider,
        launch_mode="focus",
    )
    session_name = str(launch.get("session_name") or "").strip()
    window_index = str(launch.get("window_index") or "0").strip() or "0"
    if launch.get("status") not in {"existing", "launched"}:
        fallback_session = _default_tmux_session_name(project_id=project_id, provider=selected_provider)
        if _tmux_has_session(fallback_session):
            session_name = fallback_session
            if not window_index or window_index == "0":
                window_index = "1"
        else:
            print(
                f"Failed to inspect tmux workspace for `{project_id}`: {launch.get('reason') or launch.get('status') or 'unknown error'}",
                file=sys.stderr,
            )
            return 2
    if not session_name:
        print(f"No tmux session recorded for `{project_id}`.", file=sys.stderr)
        return 2
    _ensure_tmux_session_exists(
        session_name=session_name,
        workspace_path=workspace_path,
        provider=selected_provider,
        project_id=project_id,
    )
    panes = _list_window_panes(f"{session_name}:{window_index}")
    lines = [
        f"Project: {project_id}",
        f"Provider: {selected_provider}",
        f"Session: {session_name}",
        f"Window: {window_index}",
        f"Panes: {len(panes)}",
    ]
    bridge_env = bridge_policy_env(policy)
    applied_count, missing_keys = _tmux_bridge_env_status(session_name=session_name, expected_env=bridge_env)
    lines.append(
        f"tmux-bridge env applied: {'yes' if not missing_keys else 'partial'} ({applied_count}/{len([k for k, v in bridge_env.items() if v])})"
    )
    if missing_keys:
        lines.append(f"Missing bridge env keys: {', '.join(missing_keys)}")
    for pane in panes:
        pane_id = str(pane.get("pane_id") or "")
        label = str(pane.get("label") or "")
        title = str(pane.get("title") or "")
        status = _pane_runtime_status(pane_id=pane_id, label=label)
        lines.append(f"- {pane_id} | {label or '(unlabeled)'} | {status} | {title or '(no title)'}")
    lines.append("")
    lines.append(render_bridge_policy_summary(policy))
    if exports:
        lines.append("")
        lines.append("Bridge Env:")
        for key, value in bridge_env.items():
            if value:
                lines.append(f"- {key}={value}")
    print("\n".join(lines))
    return 0


def project_sessions_open_tmux_terminal(
    project_id: str,
    *,
    provider: str | None,
    bridge_layout: bool,
    ssh_targets: list[str] | None,
    manual_pane: bool,
    secondary_agents: list[str] | None,
) -> int:
    store = ProjectContextStore()
    project = store.get_project(project_id)
    if project is None:
        print(f"Unknown project: {project_id}", file=sys.stderr)
        return 2
    workspace_path = (project.workspace_path or "").strip()
    if not workspace_path:
        print(f"Project `{project_id}` has no workspace path.", file=sys.stderr)
        return 2
    policy = load_bridge_policy(project_id, workspace_path)
    selected_provider = (provider or os.getenv("ASH_EXECUTOR") or "codex").strip().lower() or "codex"
    launch = ensure_orchestrator_pane(
        project_id=project_id,
        workspace_path=workspace_path,
        provider=selected_provider,
        launch_mode="focus",
    )
    if launch.get("status") not in {"existing", "launched"}:
        print(
            f"Failed to prepare tmux workspace for `{project_id}`: {launch.get('reason') or launch.get('status') or 'unknown error'}",
            file=sys.stderr,
        )
        return 2
    session_name = str(launch.get("session_name") or "").strip()
    if not session_name:
        print(f"No tmux session recorded for `{project_id}`.", file=sys.stderr)
        return 2
    _ensure_tmux_session_exists(
        session_name=session_name,
        workspace_path=workspace_path,
        provider=selected_provider,
        project_id=project_id,
    )
    window_index = str(launch.get("window_index") or "").strip()
    _apply_bridge_env_to_tmux_session(session_name=session_name, policy=policy)
    if bridge_layout:
        _ensure_bridge_layout(
            session_name=session_name,
            window_index=window_index or "0",
            workspace_path=workspace_path,
            project_id=project_id,
            provider=selected_provider,
            ssh_targets=ssh_targets or policy.ssh_targets,
            manual_pane=manual_pane,
            secondary_agents=secondary_agents or [],
        )
    attach_cmd = f"tmux attach -t {shlex.quote(session_name)}"
    if window_index:
        attach_cmd = (
            f"tmux select-window -t {shlex.quote(f'{session_name}:{window_index}')} >/dev/null 2>&1 || true; "
            f"tmux attach -t {shlex.quote(session_name)}"
        )
    osa_script = "\n".join(
        [
            'tell application "Terminal"',
            "  activate",
            f'  do script {json_dumps(attach_cmd)}',
            "end tell",
        ]
    )
    result = subprocess.run(
        ["osascript", "-e", osa_script],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(
            result.stderr.strip() or result.stdout.strip() or "osascript failed",
            file=sys.stderr,
        )
        return 2
    print(
        f"Opened Terminal for `{project_id}` ({selected_provider}) and attached to tmux session `{session_name}`."
    )
    print(f"Applied bridge env to tmux session `{session_name}`.")
    print(f"Bridge env: conda run -n cli python -m agent_swarm_hub.cli project-sessions bridge-env {shlex.quote(project_id)}")
    print(f"Bridge status: conda run -n cli python -m agent_swarm_hub.cli project-sessions bridge-status {shlex.quote(project_id)} --provider {shlex.quote(selected_provider)}")
    return 0


def project_sessions_bridge_workbench(
    project_id: str,
    *,
    provider: str | None,
    ssh_targets: list[str] | None,
    manual_pane: bool,
    secondary_agents: list[str] | None,
    init: bool,
    exports: bool,
) -> int:
    store = ProjectContextStore()
    project = store.get_project(project_id)
    if project is None:
        print(f"Unknown project: {project_id}", file=sys.stderr)
        return 2
    workspace_path = (project.workspace_path or "").strip()
    if not workspace_path:
        print(f"Project `{project_id}` has no workspace path.", file=sys.stderr)
        return 2
    policy = load_bridge_policy(project_id, workspace_path)
    selected_targets = [target.strip() for target in (ssh_targets or []) if str(target).strip()]
    if not selected_targets:
        selected_targets = list(policy.ssh_targets)
    exit_code = project_sessions_open_tmux_terminal(
        project_id,
        provider=provider,
        bridge_layout=True,
        ssh_targets=selected_targets,
        manual_pane=manual_pane,
        secondary_agents=secondary_agents,
    )
    if exit_code != 0:
        return exit_code
    print("")
    return project_sessions_bridge_status(
        project_id,
        provider=provider,
        init=init,
        exports=exports,
    )


def json_dumps(text: str) -> str:
    import json

    return json.dumps(text, ensure_ascii=False)


def _apply_bridge_env_to_tmux_session(*, session_name: str, policy) -> None:
    for key, value in bridge_policy_env(policy).items():
        if not value:
            continue
        _tmux_run(["set-environment", "-t", session_name, key, value])


def _tmux_bridge_env_status(*, session_name: str, expected_env: dict[str, str]) -> tuple[int, list[str]]:
    result = _tmux_run(["show-environment", "-t", session_name])
    current: dict[str, str] = {}
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line or "=" not in line or line.startswith("-"):
            continue
        key, value = line.split("=", 1)
        current[key.strip()] = value.strip()
    expected_items = {key: value for key, value in expected_env.items() if value}
    matched = 0
    missing: list[str] = []
    for key, value in expected_items.items():
        if current.get(key) == value:
            matched += 1
        else:
            missing.append(key)
    return matched, missing


def _ensure_bridge_layout(
    *,
    session_name: str,
    window_index: str,
    workspace_path: str,
    project_id: str,
    provider: str,
    ssh_targets: list[str],
    manual_pane: bool,
    secondary_agents: list[str],
) -> None:
    target_window = f"{session_name}:{window_index}"
    panes = _list_window_panes(target_window)
    agent_label = f"agent:{provider}"
    agent_pane_id = _find_pane_by_title_or_label(
        panes,
        label=agent_label,
        title_fragment=f"| {provider}",
    )
    if not agent_pane_id and panes:
        agent_pane_id = str(panes[0].get("pane_id") or "")
    if agent_pane_id:
        _tmux_run(["set-option", "-p", "-t", agent_pane_id, "@name", agent_label])
    if manual_pane and not _find_pane_by_label(panes, "manual"):
        pane_id = _tmux_create_shell_pane(target=agent_pane_id or target_window, workspace_path=workspace_path)
        _tmux_run(["set-option", "-p", "-t", pane_id, "@name", "manual"])
        panes = _list_window_panes(target_window)
    for raw_provider in secondary_agents:
        extra_provider = (raw_provider or "").strip().lower()
        if not extra_provider or extra_provider == provider:
            continue
        extra_label = f"agent:{extra_provider}"
        if _find_pane_by_label(panes, extra_label):
            continue
        pane_id = _tmux_create_agent_pane(
            target=agent_pane_id or target_window,
            workspace_path=workspace_path,
            provider=extra_provider,
            project_id=project_id,
        )
        _tmux_run(["set-option", "-p", "-t", pane_id, "@name", extra_label])
        panes = _list_window_panes(target_window)
    for raw_target in ssh_targets:
        target = (raw_target or "").strip()
        if not target:
            continue
        label = f"ssh:{target}"
        if _find_pane_by_label(panes, label):
            continue
        pane_id = _tmux_create_ssh_pane(
            target=agent_pane_id or target_window,
            workspace_path=workspace_path,
            ssh_target=target,
        )
        _tmux_run(["set-option", "-p", "-t", pane_id, "@name", label])
        panes = _list_window_panes(target_window)
    if agent_pane_id:
        _tmux_run(["set-option", "-p", "-t", agent_pane_id, "@name", agent_label])
    _tmux_run(["select-layout", "-t", target_window, "tiled"])


def _ensure_tmux_session_exists(
    *,
    session_name: str,
    workspace_path: str,
    provider: str,
    project_id: str,
) -> None:
    check = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        check=False,
        capture_output=True,
        text=True,
    )
    if check.returncode == 0:
        return
    locale_exports = "unset LANGUAGE; export LANG=C LC_ALL=C LC_CTYPE=C;"
    command = (
        f"{locale_exports} "
        f"cd {shlex.quote(str(Path(workspace_path).expanduser()))} && "
        f"cd {shlex.quote(str(Path(__file__).resolve().parents[2]))} && "
        f"ASH_AUTO_ENTER_NATIVE=1 ./scripts/start-chat.sh {shlex.quote(provider)} {shlex.quote(project_id)}; "
        f"{locale_exports} "
        f"exec {_LOGIN_SHELL} -l"
    )
    result = subprocess.run(
        [
            "tmux",
            "new-session",
            "-d",
            "-s",
            session_name,
            "-c",
            workspace_path,
            _LOGIN_SHELL,
            "-lc",
            command,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or result.stdout.strip() or f"tmux new-session failed for {session_name}"
        )


def _default_tmux_session_name(*, project_id: str, provider: str) -> str:
    normalized_project = "-".join(part for part in str(project_id).strip().replace("_", "-").split() if part)
    normalized_provider = "-".join(part for part in str(provider).strip().replace("_", "-").split() if part)
    return f"ash-{normalized_provider}-{normalized_project}"


def _tmux_has_session(session_name: str) -> bool:
    result = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _list_window_panes(target_window: str) -> list[dict[str, str]]:
    result = _tmux_run(
        [
            "list-panes",
            "-t",
            target_window,
            "-F",
            "#{pane_id}\t#{@name}\t#{pane_title}",
        ]
    )
    panes: list[dict[str, str]] = []
    for raw in result.stdout.splitlines():
        parts = raw.split("\t")
        if len(parts) != 3:
            continue
        pane_id, label, title = parts
        panes.append(
            {
                "pane_id": pane_id.strip(),
                "label": label.strip(),
                "title": title.strip(),
            }
        )
    return panes


def _pane_runtime_status(*, pane_id: str, label: str) -> str:
    if label == "manual":
        return "manual-readonly"
    preview = _tmux_capture_pane(pane_id=pane_id, lines=12).lower()
    if label.startswith("ssh:"):
        if "connection closed by" in preview or "permission denied" in preview or "could not resolve hostname" in preview:
            return "ssh-failed"
        if "@admin" in preview or "@login" in preview or "last login:" in preview:
            return "ssh-connected"
        return "ssh-shell"
    if label.startswith("agent:"):
        if "skip duplicate launch" in preview or "sunxiangrong:agent-swarm-hub" in preview or "(base)" in preview:
            return "local-shell"
        if "codex" in preview or "claude" in preview:
            return "agent-running"
        return "agent-pane"
    return "unknown"


def _tmux_capture_pane(*, pane_id: str, lines: int) -> str:
    result = _tmux_run(["capture-pane", "-pt", pane_id, "-S", f"-{max(lines, 1)}"])
    return result.stdout


def _find_pane_by_label(panes: list[dict[str, str]], label: str) -> str:
    for pane in panes:
        if pane.get("label") == label:
            return str(pane.get("pane_id") or "")
    return ""


def _find_pane_by_title_or_label(
    panes: list[dict[str, str]],
    *,
    label: str,
    title_fragment: str,
) -> str:
    by_label = _find_pane_by_label(panes, label)
    if by_label:
        return by_label
    lowered = title_fragment.lower()
    for pane in panes:
        if lowered in str(pane.get("title") or "").lower():
            return str(pane.get("pane_id") or "")
    return ""


def _tmux_create_shell_pane(*, target: str, workspace_path: str) -> str:
    result = _tmux_run(
        [
            "split-window",
            "-d",
            "-t",
            target,
            "-h",
            "-c",
            workspace_path,
            "-P",
            "-F",
            "#{pane_id}",
            _LOGIN_SHELL,
            "-l",
        ]
    )
    return result.stdout.strip().splitlines()[-1].strip()


def _tmux_create_agent_pane(*, target: str, workspace_path: str, provider: str, project_id: str) -> str:
    locale_exports = "unset LANGUAGE; export LANG=C LC_ALL=C LC_CTYPE=C;"
    repo_path = shlex.quote(str(Path(__file__).resolve().parents[2]))
    command = (
        f"{locale_exports} "
        f"cd {repo_path} && "
        f"ASH_AUTO_ENTER_NATIVE=1 ./scripts/start-chat.sh {shlex.quote(provider)} {shlex.quote(project_id)}; "
        f"{locale_exports} "
        f"exec {_LOGIN_SHELL} -l"
    )
    result = _tmux_run(
        [
            "split-window",
            "-d",
            "-t",
            target,
            "-h",
            "-c",
            workspace_path,
            "-P",
            "-F",
            "#{pane_id}",
            _LOGIN_SHELL,
            "-lc",
            command,
        ]
    )
    return result.stdout.strip().splitlines()[-1].strip()


def _tmux_create_ssh_pane(*, target: str, workspace_path: str, ssh_target: str) -> str:
    command = (
        "env -u LC_ALL -u LC_CTYPE -u LANGUAGE "
        "LANG=C LC_ALL=C LC_CTYPE=C "
        f"ssh {shlex.quote(ssh_target)} || exec {_LOGIN_SHELL} -l"
    )
    result = _tmux_run(
        [
            "split-window",
            "-d",
            "-t",
            target,
            "-v",
            "-c",
            workspace_path,
            "-P",
            "-F",
            "#{pane_id}",
            _LOGIN_SHELL,
            "-lc",
            command,
        ]
    )
    return result.stdout.strip().splitlines()[-1].strip()


def _tmux_run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["tmux", *argv],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"tmux {' '.join(argv)} failed")
    return result

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


def project_sessions_reset_current(
    project_id: str,
    provider: str,
    *,
    quarantine: bool,
    sync_project_memory_artifacts_cb,
) -> int:
    store = ProjectContextStore()
    project = store.get_project(project_id)
    if project is None:
        print(f"Unknown project: {project_id}", file=sys.stderr)
        return 2
    current = store.get_current_project_sessions(project_id)
    session_id = (current.get(provider) or "").strip()
    if not session_id:
        print(f"No current {provider} session is bound for `{project_id}`.")
        return 0
    if quarantine:
        store.quarantine_provider_session(project_id, provider, session_id)
        sync_project_memory_artifacts_cb(store, project_id)
        print(f"Quarantined current {provider} session for `{project_id}`: {session_id}")
        return 0
    store.clear_provider_binding(project_id, provider)
    sync_project_memory_artifacts_cb(store, project_id)
    print(f"Cleared current {provider} binding for `{project_id}`: {session_id}")
    return 0


def project_sessions_auto_continue_once(
    project_id: str,
    *,
    provider: str | None,
    explain: bool,
    sync_project_memory_artifacts_cb,
) -> int:
    return project_sessions_auto_continue(
        project_id,
        provider=provider,
        explain=explain,
        sync_project_memory_artifacts_cb=lambda store, pid: sync_project_memory_artifacts_cb(
            store,
            pid,
        ),
    )


def project_sessions_monitor(
    project_id: str | None,
    *,
    monitor_all: bool,
    apply: bool,
    auto_continue_enabled: bool,
    until_complete: bool,
    interval_seconds: float,
    cycles: int,
    sync_project_memory_artifacts_cb,
) -> int:
    return run_runtime_monitor(
        project_id=project_id,
        monitor_all=monitor_all,
        apply=apply,
        auto_continue_enabled=auto_continue_enabled,
        until_complete=until_complete,
        interval_seconds=interval_seconds,
        cycles=cycles,
        heartbeat_cb=lambda selected_project_id, heartbeat_all, heartbeat_apply: project_sessions_heartbeat(
            selected_project_id,
            heartbeat_all=heartbeat_all,
            apply=heartbeat_apply,
        ),
        sync_project_memory_artifacts_cb=lambda store, pid: sync_project_memory_artifacts_cb(
            store,
            pid,
        ),
    )


def project_sessions_followup_live(
    project_id: str,
    *,
    provider: str | None,
    prompt: str,
) -> int:
    try:
        result = send_followup_to_live_session(project_id, provider=provider, prompt=prompt)
    except LiveFollowupError as exc:
        print(f"Live follow-up error: {exc}", file=sys.stderr)
        return 2
    print(
        f"Sent live follow-up to {result.provider} for `{result.project_id}` "
        f"(pane={result.pane_id}{f', session={result.session_id}' if result.session_id else ''})."
    )
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


def project_sessions_heartbeat(project_id: str | None, *, heartbeat_all: bool, apply: bool) -> int:
    store = ProjectContextStore()
    if heartbeat_all:
        projects = store.list_projects()
        if not projects:
            print("No projects recorded.")
            return 0
    else:
        if not project_id:
            print("Provide a project id or use --all.", file=sys.stderr)
            return 2
        project = store.get_project(project_id)
        if project is None:
            print(f"Unknown project: {project_id}", file=sys.stderr)
            return 2
        projects = [project]

    current_codex_bindings: dict[str, str] = {}
    recorded_codex_sessions: dict[str, str] = {}
    issues = 0
    actions = 0

    for known_project in store.list_projects():
        for row in store.list_project_sessions(known_project.project_id, provider="codex", include_archived=True):
            session_id = str(row.get("session_id") or "").strip()
            if session_id and session_id not in recorded_codex_sessions:
                recorded_codex_sessions[session_id] = known_project.project_id

    for project in projects:
        current = store.get_current_project_sessions(project.project_id)
        codex_session_id = (current.get("codex") or "").strip()
        if codex_session_id:
            current_codex_bindings[codex_session_id] = project.project_id
        if not codex_session_id:
            continue

        running = find_running_codex_session(
            session_id=codex_session_id,
            work_dir=project.workspace_path,
        )
        if running is None:
            store.record_runtime_health(
                project.project_id,
                "codex",
                status="missing-binding-process",
                summary=f"Bound codex session {codex_session_id} has no running local process.",
                details={"session_id": codex_session_id, "issue": "missing-binding-process"},
            )
            print(f"[heartbeat] project={project.project_id} provider=codex status=missing-binding-process session={codex_session_id}")
            issues += 1
            if apply:
                store.clear_provider_binding(project.project_id, "codex")
                actions += 1
            continue

        health = codex_process_health(pid=running["pid"])
        if bool(health["unhealthy"]):
            store.record_runtime_health(
                project.project_id,
                "codex",
                status="quarantined" if apply else "unhealthy",
                summary=(
                    f"Codex session {codex_session_id} was detected as unhealthy "
                    f"(pid={running['pid']} cpu={health['cpu_percent']:.1f} cpu_time_s={int(health['cpu_time_seconds'])})."
                ),
                details={
                    "session_id": codex_session_id,
                    "pid": running["pid"],
                    "cpu_percent": health["cpu_percent"],
                    "cpu_time_seconds": health["cpu_time_seconds"],
                    "issue": "unhealthy",
                    "quarantined": bool(apply),
                },
            )
            print(
                f"[heartbeat] project={project.project_id} provider=codex status=unhealthy "
                f"session={codex_session_id} pid={running['pid']} cpu={health['cpu_percent']:.1f} "
                f"cpu_time_s={int(health['cpu_time_seconds'])}"
            )
            issues += 1
            if apply:
                terminate_process(running["pid"])
                store.quarantine_provider_session(project.project_id, "codex", codex_session_id)
                actions += 1
        else:
            store.record_runtime_health(
                project.project_id,
                "codex",
                status="healthy",
                summary=(
                    f"Codex session {codex_session_id} is healthy "
                    f"(pid={running['pid']} cpu={health['cpu_percent']:.1f} cpu_time_s={int(health['cpu_time_seconds'])})."
                ),
                details={
                    "session_id": codex_session_id,
                    "pid": running["pid"],
                    "cpu_percent": health["cpu_percent"],
                    "cpu_time_seconds": health["cpu_time_seconds"],
                    "issue": "healthy",
                },
            )
            print(
                f"[heartbeat] project={project.project_id} provider=codex status=healthy "
                f"session={codex_session_id} pid={running['pid']} cpu={health['cpu_percent']:.1f} "
                f"cpu_time_s={int(health['cpu_time_seconds'])}"
            )

    if heartbeat_all:
        for process in list_running_codex_processes():
            session_id = str(process.get("session_id") or "").strip()
            if not session_id or session_id in current_codex_bindings:
                continue
            owner_project = recorded_codex_sessions.get(session_id, "")
            health = codex_process_health(pid=process["pid"])
            known_detached = bool(owner_project)
            status = "detached-running" if known_detached else "orphan-running"
            print(
                f"[heartbeat] project={owner_project or 'unknown'} provider=codex status={status} "
                f"session={session_id} pid={process['pid']} cpu={health['cpu_percent']:.1f} "
                f"cpu_time_s={int(health['cpu_time_seconds'])}"
            )
            if owner_project:
                store.record_runtime_health(
                    owner_project,
                    "codex",
                    status="quarantined" if (apply and bool(health["unhealthy"])) else status,
                    summary=(
                        f"Codex session {session_id} is running outside the active binding set "
                        f"(pid={process['pid']} cpu={health['cpu_percent']:.1f} cpu_time_s={int(health['cpu_time_seconds'])})."
                    ),
                    details={
                        "session_id": session_id,
                        "pid": process["pid"],
                        "cpu_percent": health["cpu_percent"],
                        "cpu_time_seconds": health["cpu_time_seconds"],
                        "issue": status,
                        "quarantined": bool(apply and bool(health["unhealthy"])),
                        "orphan": not known_detached,
                        "detached": known_detached,
                    },
                )
            if not known_detached or bool(health["unhealthy"]):
                issues += 1
            if apply and bool(health["unhealthy"]):
                terminate_process(process["pid"])
                if owner_project:
                    store.quarantine_provider_session(owner_project, "codex", session_id)
                    actions += 1

    if issues == 0:
        print("No provider heartbeat issues detected.")
    else:
        mode = "applied" if apply else "dry-run"
        print(f"Heartbeat issues: {issues} | actions: {actions} | mode: {mode}")
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
