from __future__ import annotations

from dataclasses import dataclass

from .models import Event, EventType


@dataclass(frozen=True, slots=True)
class EscalationDecision:
    should_escalate: bool
    level: str | None = None
    reason: str | None = None


class EscalationPolicy:
    """Translate internal swarm events into remote-visible escalation decisions."""

    _EVENT_LEVELS = {
        EventType.TASK_BLOCKED: "BLOCKER",
        EventType.RISK_DETECTED: "RISK",
        EventType.DISSENT_RAISED: "DISSENT",
        EventType.NEED_INPUT: "REQUEST_INPUT",
    }

    def evaluate(self, event: Event) -> EscalationDecision:
        level = self._EVENT_LEVELS.get(event.type)
        if level:
            return EscalationDecision(
                should_escalate=True,
                level=level,
                reason=event.summary,
            )
        if event.type is EventType.FINAL_CANDIDATE_READY and "important" in event.summary.lower():
            return EscalationDecision(
                should_escalate=True,
                level="IMPORTANT_FINDING",
                reason=event.summary,
            )
        return EscalationDecision(should_escalate=False)
