from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_DENY_PREFIXES = ["rm -rf", "sudo", "reboot", "shutdown"]
DEFAULT_READONLY_PATHS = ["/", "/etc", "/usr", "/var"]
DEFAULT_AGENT_LABELS = ["agent:codex", "agent:claude"]


@dataclass(frozen=True)
class BridgePolicy:
    project_id: str
    workspace_path: str
    ssh_targets: list[str]
    readable_targets: list[str]
    writable_targets: list[str]
    readonly_paths: list[str]
    writable_paths: list[str]
    deny_prefixes: list[str]
    allow_manual_write: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "workspace_path": self.workspace_path,
            "ssh_targets": list(self.ssh_targets),
            "readable_targets": list(self.readable_targets),
            "writable_targets": list(self.writable_targets),
            "readonly_paths": list(self.readonly_paths),
            "writable_paths": list(self.writable_paths),
            "deny_prefixes": list(self.deny_prefixes),
            "allow_manual_write": bool(self.allow_manual_write),
        }


def bridge_policy_path(workspace_path: str | Path) -> Path:
    workspace = Path(workspace_path).expanduser()
    return workspace / ".ash" / "bridge-policy.json"


def default_bridge_policy(project_id: str, workspace_path: str) -> BridgePolicy:
    ssh_targets = ["xinong", "ias"]
    readable_targets = _default_readable_targets(ssh_targets)
    writable_targets = _default_writable_targets(ssh_targets)
    writable_paths = [workspace_path] if workspace_path else []
    return BridgePolicy(
        project_id=project_id,
        workspace_path=workspace_path,
        ssh_targets=ssh_targets,
        readable_targets=readable_targets,
        writable_targets=writable_targets,
        readonly_paths=list(DEFAULT_READONLY_PATHS),
        writable_paths=writable_paths,
        deny_prefixes=list(DEFAULT_DENY_PREFIXES),
        allow_manual_write=False,
    )


def load_bridge_policy(project_id: str, workspace_path: str) -> BridgePolicy:
    path = bridge_policy_path(workspace_path)
    if not path.exists():
        return default_bridge_policy(project_id, workspace_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    default = default_bridge_policy(project_id, workspace_path)
    ssh_targets = _string_list(payload.get("ssh_targets"), default.ssh_targets)
    readable_targets = _sync_targets(
        _string_list(payload.get("readable_targets"), default.readable_targets),
        ssh_targets=ssh_targets,
        include_manual=True,
    )
    writable_targets = _sync_targets(
        _string_list(payload.get("writable_targets"), default.writable_targets),
        ssh_targets=ssh_targets,
        include_manual=False,
    )
    return BridgePolicy(
        project_id=str(payload.get("project_id") or default.project_id),
        workspace_path=str(payload.get("workspace_path") or default.workspace_path),
        ssh_targets=ssh_targets,
        readable_targets=readable_targets,
        writable_targets=writable_targets,
        readonly_paths=_string_list(payload.get("readonly_paths"), default.readonly_paths),
        writable_paths=_string_list(payload.get("writable_paths"), default.writable_paths),
        deny_prefixes=_string_list(payload.get("deny_prefixes"), default.deny_prefixes),
        allow_manual_write=bool(payload.get("allow_manual_write", default.allow_manual_write)),
    )


def init_bridge_policy(project_id: str, workspace_path: str, *, force: bool = False) -> Path:
    path = bridge_policy_path(workspace_path)
    if path.exists() and not force:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = default_bridge_policy(project_id, workspace_path).to_dict()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def save_bridge_policy(policy: BridgePolicy) -> Path:
    path = bridge_policy_path(policy.workspace_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(policy.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def update_bridge_policy(
    project_id: str,
    workspace_path: str,
    *,
    ssh_targets: list[str] | None = None,
) -> BridgePolicy:
    current = load_bridge_policy(project_id, workspace_path)
    next_ssh_targets = _string_list(ssh_targets, current.ssh_targets) if ssh_targets is not None else list(current.ssh_targets)
    updated = BridgePolicy(
        project_id=current.project_id,
        workspace_path=current.workspace_path,
        ssh_targets=next_ssh_targets,
        readable_targets=_sync_targets(list(current.readable_targets), ssh_targets=next_ssh_targets, include_manual=True),
        writable_targets=_sync_targets(list(current.writable_targets), ssh_targets=next_ssh_targets, include_manual=False),
        readonly_paths=list(current.readonly_paths),
        writable_paths=list(current.writable_paths),
        deny_prefixes=list(current.deny_prefixes),
        allow_manual_write=bool(current.allow_manual_write),
    )
    save_bridge_policy(updated)
    return updated


def bridge_policy_env(policy: BridgePolicy) -> dict[str, str]:
    env = {
        "TMUX_BRIDGE_READABLE_TARGETS": _csv(policy.readable_targets),
        "TMUX_BRIDGE_WRITABLE_TARGETS": _csv(policy.writable_targets),
        "TMUX_BRIDGE_READONLY_PATHS": _csv(policy.readonly_paths),
        "TMUX_BRIDGE_WRITABLE_PATHS": _csv(policy.writable_paths),
        "TMUX_BRIDGE_DENY_PREFIXES": _csv(policy.deny_prefixes),
    }
    if policy.allow_manual_write:
        env["TMUX_BRIDGE_ALLOW_MANUAL_WRITE"] = "1"
    return env


def render_bridge_policy_summary(policy: BridgePolicy, *, path: Path | None = None) -> str:
    lines = [
            f"Project: {policy.project_id}",
            f"Workspace: {policy.workspace_path}",
        ]
    if path is not None:
        lines.append(f"Policy File: {path}")
    lines.extend(
        [
            f"SSH Targets: {', '.join(policy.ssh_targets) or '(none)'}",
            f"Readable Targets: {', '.join(policy.readable_targets) or '(none)'}",
            f"Writable Targets: {', '.join(policy.writable_targets) or '(none)'}",
            f"Readonly Paths: {', '.join(policy.readonly_paths) or '(none)'}",
            f"Writable Paths: {', '.join(policy.writable_paths) or '(none)'}",
            f"Deny Prefixes: {', '.join(policy.deny_prefixes) or '(none)'}",
            f"Allow Manual Write: {'yes' if policy.allow_manual_write else 'no'}",
        ]
    )
    return "\n".join(lines)


def render_bridge_env_exports(policy: BridgePolicy) -> str:
    env = bridge_policy_env(policy)
    lines = [f"export {key}={shlex.quote(value)}" for key, value in env.items() if value]
    return "\n".join(lines)


def _csv(values: list[str]) -> str:
    return ",".join(item.strip() for item in values if item and item.strip())


def _string_list(value: Any, fallback: list[str]) -> list[str]:
    if not isinstance(value, list):
        return list(fallback)
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result or list(fallback)


def _default_ssh_labels(ssh_targets: list[str]) -> list[str]:
    return [f"ssh:{target}" for target in ssh_targets if str(target).strip()]


def _default_readable_targets(ssh_targets: list[str]) -> list[str]:
    return ["manual", *DEFAULT_AGENT_LABELS, *_default_ssh_labels(ssh_targets)]


def _default_writable_targets(ssh_targets: list[str]) -> list[str]:
    return [*DEFAULT_AGENT_LABELS, *_default_ssh_labels(ssh_targets)]


def _sync_targets(values: list[str], *, ssh_targets: list[str], include_manual: bool) -> list[str]:
    ssh_labels = _default_ssh_labels(ssh_targets)
    preserved = [item for item in values if not item.startswith("ssh:")]
    ordered: list[str] = []
    if include_manual:
        ordered.append("manual")
    ordered.extend(DEFAULT_AGENT_LABELS)
    for item in preserved:
        if item not in ordered:
            ordered.append(item)
    for label in ssh_labels:
        if label not in ordered:
            ordered.append(label)
    return ordered
