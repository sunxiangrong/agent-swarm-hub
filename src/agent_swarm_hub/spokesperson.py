from __future__ import annotations

from .models import Event, Task, TaskStatus


class Spokesperson:
    """Render concise remote-facing summaries from swarm state."""

    def summarize(
        self,
        *,
        root_task: Task,
        tasks: list[Task],
        latest_events: list[Event],
    ) -> str:
        counts = {
            status: sum(1 for task in tasks if task.status is status)
            for status in TaskStatus
        }
        recent = latest_events[-3:]
        progress_bits = [event.summary for event in recent if event.summary]
        progress = "; ".join(progress_bits) if progress_bits else "No notable updates yet."
        return (
            f"Task: {root_task.title}\n"
            f"Stage: {root_task.status.value}\n"
            f"Tasks: pending={counts[TaskStatus.PENDING]}, "
            f"in_progress={counts[TaskStatus.IN_PROGRESS]}, "
            f"blocked={counts[TaskStatus.BLOCKED]}, "
            f"completed={counts[TaskStatus.COMPLETED]}\n"
            f"Recent: {progress}"
        )
