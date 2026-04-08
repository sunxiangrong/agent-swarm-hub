from __future__ import annotations
"""Runtime monitor loop for continuous heartbeat and bounded auto-resume.

This module sits above runtime-health probes and below chat adapters. It keeps
the first continuous automation loop intentionally narrow:

- run heartbeat probes on a schedule
- optionally apply repair actions
- optionally trigger one single-step auto-continue per eligible project/cycle
"""

import time
from typing import Callable

from .auto_continue import (
    build_auto_continue_plan,
    evaluate_auto_continue_completion,
    project_sessions_auto_continue,
)
from .project_context import ProjectContextStore


def parse_runtime_monitor_request(argument: str) -> dict[str, object]:
    tokens = [token.strip() for token in (argument or "").split() if token.strip()]
    apply = False
    auto_continue_enabled = False
    until_complete = False
    interval_seconds = 30.0
    cycles = 1
    idx = 0
    while idx < len(tokens):
        token = tokens[idx].lower()
        if token == "--apply":
            apply = True
            idx += 1
            continue
        if token == "--auto-continue":
            auto_continue_enabled = True
            idx += 1
            continue
        if token in {"--until-complete", "--goal-driven"}:
            until_complete = True
            idx += 1
            continue
        if token == "--interval" and idx + 1 < len(tokens):
            try:
                interval_seconds = float(tokens[idx + 1])
            except ValueError:
                interval_seconds = 30.0
            idx += 2
            continue
        if token == "--cycles" and idx + 1 < len(tokens):
            try:
                cycles = int(tokens[idx + 1])
            except ValueError:
                cycles = 1
            idx += 2
            continue
        idx += 1
    return {
        "apply": apply,
        "auto_continue_enabled": auto_continue_enabled,
        "until_complete": until_complete,
        "interval_seconds": interval_seconds,
        "cycles": cycles,
    }


def run_runtime_monitor(
    *,
    project_id: str | None,
    monitor_all: bool,
    apply: bool,
    auto_continue_enabled: bool,
    until_complete: bool,
    interval_seconds: float,
    cycles: int,
    heartbeat_cb: Callable[[str | None, bool, bool], int],
    sync_project_memory_artifacts_cb: Callable[[ProjectContextStore, str], None],
) -> int:
    cycle_count = max(1, int(cycles or 1))
    interval = max(1.0, float(interval_seconds or 0.0))
    exit_code = 0

    for index in range(cycle_count):
        current_cycle = index + 1
        print(f"[monitor] cycle={current_cycle}/{cycle_count} heartbeat")
        heartbeat_code = heartbeat_cb(project_id, monitor_all, apply)
        if heartbeat_code != 0:
            exit_code = heartbeat_code

        attempted_targets = 0
        executed_targets = 0
        blocked_targets = 0
        settled_targets = 0
        terminal_targets = 0
        if auto_continue_enabled:
            store = ProjectContextStore()
            if monitor_all:
                targets = [item.project_id for item in store.list_projects()]
            else:
                targets = [project_id] if project_id else []
            for target in targets:
                if not target:
                    continue
                attempted_targets += 1
                plan = build_auto_continue_plan(target, context_store=store)
                if int(plan.get("code", 0)) != 0:
                    blocked_targets += 1
                    continue
                if not str(plan.get("prompt") or "").strip():
                    settled_targets += 1
                    store.record_auto_continue_state(
                        target,
                        "codex",
                        status="settled",
                        summary=str(plan.get("message") or "No auto-continue candidate is available."),
                        details={
                            "mode": "monitor",
                            "cycle": current_cycle,
                            "reason": "no-candidate",
                        },
                    )
                    continue
                print(f"[monitor] cycle={current_cycle}/{cycle_count} auto-continue project={target}")
                result = project_sessions_auto_continue(
                    target,
                    provider=None,
                    explain=False,
                    sync_project_memory_artifacts_cb=sync_project_memory_artifacts_cb,
                )
                executed_targets += 1
                if result != 0 and exit_code == 0:
                    exit_code = result
                    blocked_targets += 1
                    continue
                verdict = evaluate_auto_continue_completion(
                    target,
                    provider=None,
                    context_store=store,
                )
                verdict_status = str(verdict.get("status") or "active").strip().lower() or "active"
                verdict_provider = str(verdict.get("provider") or "codex").strip().lower() or "codex"
                verdict_reason = str(verdict.get("reason") or "").strip()
                if verdict_status in {"completed", "blocked", "needs_confirmation"}:
                    terminal_targets += 1
                store.record_auto_continue_state(
                    target,
                    verdict_provider,
                    status=verdict_status,
                    summary=verdict_reason or f"Auto-monitor completion verdict: {verdict_status}",
                    details={
                        "mode": "monitor",
                        "cycle": current_cycle,
                        "provider": verdict_provider,
                        "status": verdict_status,
                        "next_step": str(verdict.get("next_step") or ""),
                        "blocker": str(verdict.get("blocker") or ""),
                        "needs_confirmation": bool(verdict.get("needs_confirmation") or False),
                        "backend": str(verdict.get("backend") or ""),
                    },
                )

        if until_complete and auto_continue_enabled:
            if attempted_targets > 0 and executed_targets == 0 and blocked_targets == 0:
                print(f"[monitor] cycle={current_cycle}/{cycle_count} stopping early: no further auto-continue candidates remain")
                break
            if executed_targets > 0 and terminal_targets == executed_targets:
                print(f"[monitor] cycle={current_cycle}/{cycle_count} stopping early: auto-continue reached a terminal completion verdict")
                break

        if current_cycle < cycle_count:
            time.sleep(interval)

    return exit_code
