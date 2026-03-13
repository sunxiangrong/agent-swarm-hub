from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"


class EventType(str, Enum):
    TASK_STARTED = "task_started"
    TASK_SPLIT = "task_split"
    TASK_COMPLETED = "task_completed"
    TASK_BLOCKED = "task_blocked"
    RISK_DETECTED = "risk_detected"
    DISSENT_RAISED = "dissent_raised"
    NEED_INPUT = "need_input"
    FINAL_CANDIDATE_READY = "final_candidate_ready"


@dataclass(slots=True)
class Task:
    id: str
    title: str
    role: str
    status: TaskStatus = TaskStatus.PENDING
    parent_id: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Event:
    type: EventType
    task_id: str
    role: str
    summary: str
    details: str = ""
