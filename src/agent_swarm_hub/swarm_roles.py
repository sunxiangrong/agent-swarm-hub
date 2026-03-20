from __future__ import annotations

from dataclasses import dataclass


_SUPPORTED = {"claude", "codex"}


@dataclass(frozen=True, slots=True)
class SwarmRoles:
    trigger: str
    orchestrator: str
    planner: str
    executor: str
    reviewer: str


def resolve_swarm_roles(preferred_trigger: str | None) -> SwarmRoles:
    trigger = (preferred_trigger or "").strip().lower()
    if trigger not in _SUPPORTED:
        trigger = "claude"
    return SwarmRoles(
        trigger=trigger,
        orchestrator="claude",
        planner="claude",
        executor="codex",
        reviewer="claude",
    )
