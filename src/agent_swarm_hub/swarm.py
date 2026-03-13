from __future__ import annotations

from dataclasses import dataclass, field

from .escalation import EscalationDecision, EscalationPolicy
from .models import Event, EventType, Task, TaskStatus
from .spokesperson import Spokesperson


@dataclass(slots=True)
class SwarmState:
    root_task_id: str
    tasks: dict[str, Task] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)


class SwarmCoordinator:
    """Minimal swarm coordinator for task graph updates and remote summaries."""

    def __init__(
        self,
        *,
        escalation_policy: EscalationPolicy | None = None,
        spokesperson: Spokesperson | None = None,
    ):
        self.escalation_policy = escalation_policy or EscalationPolicy()
        self.spokesperson = spokesperson or Spokesperson()

    def create_root_task(self, *, task_id: str, title: str, role: str = "coordinator") -> SwarmState:
        root = Task(id=task_id, title=title, role=role)
        return SwarmState(root_task_id=task_id, tasks={task_id: root})

    def split_task(
        self,
        state: SwarmState,
        *,
        parent_id: str,
        children: list[tuple[str, str, str]],
    ) -> Event:
        parent = state.tasks[parent_id]
        parent.status = TaskStatus.IN_PROGRESS
        for task_id, title, role in children:
            state.tasks[task_id] = Task(id=task_id, title=title, role=role, parent_id=parent_id)
        event = Event(
            type=EventType.TASK_SPLIT,
            task_id=parent_id,
            role=parent.role,
            summary=f"Split into {len(children)} specialized task(s).",
        )
        state.events.append(event)
        return event

    def record_event(self, state: SwarmState, event: Event) -> EscalationDecision:
        task = state.tasks[event.task_id]
        if event.type is EventType.TASK_STARTED:
            task.status = TaskStatus.IN_PROGRESS
        elif event.type is EventType.TASK_COMPLETED:
            task.status = TaskStatus.COMPLETED
        elif event.type in (EventType.TASK_BLOCKED, EventType.NEED_INPUT):
            task.status = TaskStatus.BLOCKED
        state.events.append(event)
        return self.escalation_policy.evaluate(event)

    def render_remote_summary(self, state: SwarmState) -> str:
        root_task = state.tasks[state.root_task_id]
        if root_task.status is TaskStatus.PENDING and any(
            task.status is TaskStatus.IN_PROGRESS for task in state.tasks.values()
        ):
            root_task.status = TaskStatus.IN_PROGRESS
        if all(
            task.status is TaskStatus.COMPLETED
            for task in state.tasks.values()
            if task.id != state.root_task_id
        ) and len(state.tasks) > 1:
            root_task.status = TaskStatus.COMPLETED
        return self.spokesperson.summarize(
            root_task=root_task,
            tasks=list(state.tasks.values()),
            latest_events=state.events,
        )
