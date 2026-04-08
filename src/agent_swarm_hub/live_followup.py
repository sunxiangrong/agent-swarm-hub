from __future__ import annotations

"""Send a follow-up prompt into an already-live provider conversation.

This module powers the smallest useful form of "resume by nudging the current
dialogue": if the provider pane is still alive, inject one more prompt into the
same running conversation instead of opening a fresh execution path.
"""

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import ccb_lib_dir
from .project_context import ProjectContextStore


class LiveFollowupError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LiveFollowupResult:
    project_id: str
    provider: str
    pane_id: str
    session_id: str
    workspace_path: str


def _provider_session_module(provider: str) -> str:
    normalized = (provider or "").strip().lower()
    if normalized == "codex":
        return "caskd_session"
    if normalized == "claude":
        return "laskd_session"
    raise LiveFollowupError(f"Unsupported live follow-up provider: {provider}")


def _load_provider_project_session(*, provider: str, work_dir: Path) -> Any | None:
    lib_dir = ccb_lib_dir()
    if not lib_dir.exists():
        raise LiveFollowupError(f"CCB library directory is not available: {lib_dir}")
    module_name = _provider_session_module(provider)
    cwd_before = Path.cwd()
    try:
        sys.path.insert(0, str(lib_dir))
        work_dir = work_dir.resolve()
        module = importlib.import_module(module_name)
        loader = getattr(module, "load_project_session", None)
        if not callable(loader):
            raise LiveFollowupError(f"{module_name}.load_project_session is not available")
        return loader(work_dir)
    except LiveFollowupError:
        raise
    except Exception as exc:
        raise LiveFollowupError(f"Failed to load {provider} project session: {exc}") from exc
    finally:
        try:
            sys.path = [entry for entry in sys.path if entry != str(lib_dir)]
        except Exception:
            pass
        try:
            Path.cwd()
        except Exception:
            pass
        try:
            import os

            os.chdir(cwd_before)
        except Exception:
            pass


def send_followup_to_live_session(
    project_id: str,
    *,
    provider: str | None = None,
    prompt: str,
    context_store: ProjectContextStore | None = None,
) -> LiveFollowupResult:
    store = context_store or ProjectContextStore()
    project = store.get_project(project_id)
    if project is None:
        raise LiveFollowupError(f"Unknown project: {project_id}")
    workspace_path = str(project.workspace_path or "").strip()
    if not workspace_path:
        raise LiveFollowupError(f"Project `{project_id}` has no workspace path")
    prompt_text = (prompt or "").strip()
    if not prompt_text:
        raise LiveFollowupError("Follow-up prompt is empty")

    current_sessions = store.get_current_project_sessions(project_id)
    resolved_provider = (provider or "").strip().lower()
    if not resolved_provider:
        resolved_provider = (
            "codex"
            if current_sessions.get("codex")
            else next(iter(current_sessions.keys()), "codex")
        )

    session = _load_provider_project_session(provider=resolved_provider, work_dir=Path(workspace_path))
    if session is None:
        raise LiveFollowupError(
            f"No live {resolved_provider} project session metadata found under `{workspace_path}`"
        )

    ensure_pane = getattr(session, "ensure_pane", None)
    if not callable(ensure_pane):
        raise LiveFollowupError(f"{resolved_provider} session object cannot ensure a live pane")
    ok, pane_or_reason = ensure_pane()
    if not ok:
        reason = str(pane_or_reason or f"{resolved_provider} pane is not alive").strip()
        if "no server running" in reason.lower() or "pane not alive" in reason.lower():
            reason = (
                f"{reason}. "
                f"Live follow-up currently requires a bridge-managed tmux/wezterm pane; "
                f"a raw standalone {resolved_provider} UI conversation cannot be injected yet."
            )
        raise LiveFollowupError(reason)
    pane_id = str(pane_or_reason or "").strip()
    if not pane_id:
        raise LiveFollowupError(f"{resolved_provider} live pane id is empty")

    backend_factory = getattr(session, "backend", None)
    backend = backend_factory() if callable(backend_factory) else None
    send_text = getattr(backend, "send_text", None)
    if not callable(send_text):
        raise LiveFollowupError(f"{resolved_provider} backend cannot send text into the live pane")
    try:
        send_text(pane_id, prompt_text)
    except Exception as exc:
        raise LiveFollowupError(f"Failed to inject live follow-up into pane {pane_id}: {exc}") from exc

    session_id = ""
    if resolved_provider == "codex":
        session_id = str(getattr(session, "codex_session_id", "") or current_sessions.get("codex") or "").strip()
    elif resolved_provider == "claude":
        session_id = str(current_sessions.get("claude") or "").strip()

    return LiveFollowupResult(
        project_id=project_id,
        provider=resolved_provider,
        pane_id=pane_id,
        session_id=session_id,
        workspace_path=workspace_path,
    )
