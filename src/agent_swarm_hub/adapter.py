from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha1

from .escalation import EscalationDecision
from .models import Event
from .remote import RemoteMessage, parse_remote_command
from .swarm import SwarmCoordinator, SwarmState


@dataclass(slots=True)
class AdapterResponse:
    text: str
    task_id: str | None = None
    escalation: EscalationDecision | None = None
    visible_events: list[Event] = field(default_factory=list)


class CCConnectAdapter:
    """Translate remote chat messages into runtime coordinator actions."""

    def __init__(self, coordinator: SwarmCoordinator | None = None):
        self.coordinator = coordinator or SwarmCoordinator()
        self.sessions: dict[str, SwarmState] = {}
        self.escalations: dict[str, list[Event]] = {}

    def handle_message(self, message: RemoteMessage) -> AdapterResponse:
        command = parse_remote_command(message.text)
        if command.name == "help":
            return AdapterResponse(
                text=(
                    "Commands:\n"
                    "/write <task>\n"
                    "/status\n"
                    "/escalations\n"
                    "/help"
                )
            )
        if command.name == "write":
            return self._handle_write(message, command.argument)
        if command.name == "status":
            return self._handle_status(message)
        return self._handle_escalations(message)

    def publish_event(self, message: RemoteMessage, event: Event) -> AdapterResponse:
        state = self.sessions[message.session_key]
        decision = self.coordinator.record_event(state, event)
        visible_events: list[Event] = []
        if decision.should_escalate:
            visible_events.append(event)
            self.escalations.setdefault(message.session_key, []).append(event)
        return AdapterResponse(
            text=self.coordinator.render_remote_summary(state),
            task_id=state.root_task_id,
            escalation=decision,
            visible_events=visible_events,
        )

    def _handle_write(self, message: RemoteMessage, argument: str) -> AdapterResponse:
        if not argument:
            return AdapterResponse(text="Usage: /write <task>")
        task_id = self._make_task_id(message, argument)
        state = self.coordinator.create_root_task(task_id=task_id, title=argument, role="runtime_coordinator")
        self.sessions[message.session_key] = state
        self.escalations.setdefault(message.session_key, [])
        return AdapterResponse(
            text=(
                f"Accepted task.\n"
                f"Task ID: {task_id}\n"
                f"{self.coordinator.render_remote_summary(state)}"
            ),
            task_id=task_id,
        )

    def _handle_status(self, message: RemoteMessage) -> AdapterResponse:
        state = self.sessions.get(message.session_key)
        if state is None:
            return AdapterResponse(text="No active task in this chat yet. Use /write <task> first.")
        summary = self.coordinator.render_remote_summary(state)
        return AdapterResponse(
            text=f"Task ID: {state.root_task_id}\n{summary}",
            task_id=state.root_task_id,
        )

    def _handle_escalations(self, message: RemoteMessage) -> AdapterResponse:
        state = self.sessions.get(message.session_key)
        if state is None:
            return AdapterResponse(text="No active task in this chat yet. Use /write <task> first.")
        escalated = self.escalations.get(message.session_key, [])
        if not escalated:
            return AdapterResponse(
                text=f"Task ID: {state.root_task_id}\nNo escalations so far.",
                task_id=state.root_task_id,
            )
        lines = [f"Task ID: {state.root_task_id}", "Escalations:"]
        lines.extend(f"- [{event.role}] {event.summary}" for event in escalated[-5:])
        return AdapterResponse(text="\n".join(lines), task_id=state.root_task_id)

    def _make_task_id(self, message: RemoteMessage, argument: str) -> str:
        digest = sha1(f"{message.session_key}:{argument}".encode("utf-8")).hexdigest()
        return digest[:12]
