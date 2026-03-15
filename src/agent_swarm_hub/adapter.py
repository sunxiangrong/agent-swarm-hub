from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any

from .escalation import EscalationDecision
from .executor import Executor, ExecutorError
from .models import Event
from .project_context import ProjectContextStore
from .remote import RemoteMessage, parse_remote_command
from .session_store import SessionStore
from .swarm import SwarmCoordinator, SwarmState
from .worker_session import LocalExecutorSessionPool

PHASE_DISCUSSION = "discussion"
PHASE_READY = "ready_for_execution"
PHASE_EXECUTING = "executing"
PHASE_REVIEWING = "reviewing"
PHASE_REPORTED = "reported"
LOW_SIGNAL_TEXT = {"hi", "hello", "ok", "okay", "好的", "收到", "继续", "开始"}


@dataclass(slots=True)
class AdapterResponse:
    text: str
    task_id: str | None = None
    escalation: EscalationDecision | None = None
    visible_events: list[Event] = field(default_factory=list)


class CCConnectAdapter:
    """Translate remote chat messages into runtime coordinator actions."""

    def __init__(
        self,
        coordinator: SwarmCoordinator | None = None,
        executor: Executor | None = None,
        store: SessionStore | None = None,
        worker_pool: LocalExecutorSessionPool | None = None,
    ):
        self.coordinator = coordinator or SwarmCoordinator()
        self.executor = executor
        self.store = store or SessionStore()
        self.worker_pool = worker_pool or LocalExecutorSessionPool()
        self.project_context_store = ProjectContextStore()
        self.sessions: dict[str, SwarmState] = {}
        self.escalations: dict[str, list[Event]] = {}

    def handle_message(self, message: RemoteMessage) -> AdapterResponse:
        workspace_id = self._get_or_create_bound_workspace(message)
        self._ensure_executor_session_id(message.session_key, workspace_id)
        self._load_session(message.session_key, workspace_id)
        if self._should_treat_as_ephemeral(message, workspace_id):
            return self._handle_ephemeral(message, workspace_id)
        if message.text.strip() and not message.text.strip().startswith("/") and self._has_active_task(message.session_key, workspace_id):
            return self._handle_continue(message, message.text.strip())
        command = parse_remote_command(message.text)
        if command.name == "help":
            return AdapterResponse(
                text=(
                    "Commands:\n"
                    "/projects\n"
                    "/use <workspace>\n"
                    "/where\n"
                    "/write <task>\n"
                    "/execute [notes]\n"
                    "/new\n"
                    "/status\n"
                    "/sessions\n"
                    "/escalations\n"
                    "/help"
                )
            )
        if command.name == "projects":
            return self._handle_projects(message, workspace_id)
        if command.name == "use":
            return self._handle_use(message, command.argument)
        if command.name == "where":
            return self._handle_where(message, workspace_id)
        if command.name == "project":
            return self._handle_project(message, workspace_id, command.argument)
        if command.name == "write":
            return self._handle_write(message, command.argument)
        if command.name == "execute":
            return self._handle_execute(message, command.argument)
        if command.name == "new":
            return self._handle_new(message)
        if command.name == "status":
            return self._handle_status(message)
        if command.name == "sessions":
            return self._handle_sessions(message)
        return self._handle_escalations(message)

    def publish_event(self, message: RemoteMessage, event: Event) -> AdapterResponse:
        workspace_id = self._get_or_create_bound_workspace(message)
        state = self._load_session(message.session_key, workspace_id)
        if state is None:
            raise KeyError(f"No active session for {message.session_key} in workspace {workspace_id}")
        decision = self.coordinator.record_event(state, event)
        visible_events: list[Event] = []
        if decision.should_escalate:
            visible_events.append(event)
            self.escalations.setdefault(self._memory_key(message.session_key, workspace_id), []).append(event)
        self._persist_session(message, workspace_id, state)
        return AdapterResponse(
            text=self.coordinator.render_remote_summary(state),
            task_id=state.root_task_id,
            escalation=decision,
            visible_events=visible_events,
        )

    def _handle_write(self, message: RemoteMessage, argument: str) -> AdapterResponse:
        if not argument:
            return AdapterResponse(text="Usage: /write <task>")
        workspace_id = self._get_or_create_bound_workspace(message)
        executor_session_id = self._ensure_executor_session_id(message.session_key, workspace_id)
        task_id = self._make_task_id(message, argument)
        state = self.coordinator.create_root_task(task_id=task_id, title=argument, role="runtime_coordinator")
        memory_key = self._memory_key(message.session_key, workspace_id)
        self.sessions[memory_key] = state
        self.escalations.setdefault(memory_key, [])
        self.store.append_message(
            session_key=memory_key,
            task_id=task_id,
            role="user",
            platform_message_id=message.message_id,
            text=argument,
        )
        self.store.append_agent_message(
            session_key=message.session_key,
            workspace_id=workspace_id,
            agent="claude",
            task_id=task_id,
            role="user",
            text=argument,
        )
        try:
            result = self._run_agent_prompt(
                session_key=message.session_key,
                workspace_id=workspace_id,
                phase=PHASE_DISCUSSION,
                mode="claude",
                prompt=argument,
            )
            text = f"Task ID: {task_id}\nPhase: {PHASE_DISCUSSION}\nBackend: {result.backend}\n{result.output}"
        except ExecutorError as exc:
            text = f"Task ID: {task_id}\nPhase: {PHASE_DISCUSSION}\nExecution error: {exc}\n{self.coordinator.render_remote_summary(state)}"
        self.store.append_message(session_key=memory_key, task_id=task_id, role="assistant", text=text)
        self.store.append_agent_message(
            session_key=message.session_key,
            workspace_id=workspace_id,
            agent="claude",
            task_id=task_id,
            role="assistant",
            text=text,
        )
        self._persist_session(
            message,
            workspace_id,
            state,
            executor_session_id=executor_session_id,
            phase=PHASE_DISCUSSION,
        )
        return AdapterResponse(text=text, task_id=task_id)

    def _handle_ephemeral(self, message: RemoteMessage, workspace_id: str) -> AdapterResponse:
        text = message.text.strip()
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        self.store.append_ephemeral_message(
            session_key=message.session_key,
            workspace_id=workspace_id,
            agent="claude",
            role="user",
            text=text,
            expires_at=expires_at,
        )
        self.store.trim_ephemeral_messages(message.session_key, workspace_id, "claude", keep=5)
        return AdapterResponse(
            text=(
                f"Workspace: {workspace_id}\n"
                "This short message was kept as ephemeral context only.\n"
                "It will not enter project summary or long-term session memory."
            )
        )

    def _handle_continue(self, message: RemoteMessage, argument: str) -> AdapterResponse:
        workspace_id = self._get_or_create_bound_workspace(message)
        executor_session_id = self._ensure_executor_session_id(message.session_key, workspace_id)
        memory_key = self._memory_key(message.session_key, workspace_id)
        state = self._load_session(message.session_key, workspace_id)
        if state is None:
            return self._handle_write(message, argument)
        task_id = state.root_task_id
        phase = self._phase_for_followup(message.session_key, workspace_id)
        self.store.append_message(
            session_key=memory_key,
            task_id=task_id,
            role="user",
            platform_message_id=message.message_id,
            text=argument,
        )
        self.store.append_agent_message(
            session_key=message.session_key,
            workspace_id=workspace_id,
            agent="claude",
            task_id=task_id,
            role="user",
            text=argument,
        )
        try:
            result = self._run_agent_prompt(
                session_key=message.session_key,
                workspace_id=workspace_id,
                phase=phase,
                mode="claude",
                prompt=argument,
            )
            text = f"Task ID: {task_id}\nPhase: {phase}\nBackend: {result.backend}\n{result.output}"
        except ExecutorError as exc:
            text = f"Task ID: {task_id}\nPhase: {phase}\nExecution error: {exc}\n{self.coordinator.render_remote_summary(state)}"
        self.store.append_message(session_key=memory_key, task_id=task_id, role="assistant", text=text)
        self.store.append_agent_message(
            session_key=message.session_key,
            workspace_id=workspace_id,
            agent="claude",
            task_id=task_id,
            role="assistant",
            text=text,
        )
        self._persist_session(
            message,
            workspace_id,
            state,
            executor_session_id=executor_session_id,
            phase=phase,
        )
        return AdapterResponse(text=text, task_id=task_id)

    def _handle_execute(self, message: RemoteMessage, argument: str) -> AdapterResponse:
        workspace_id = self._get_or_create_bound_workspace(message)
        executor_session_id = self._ensure_executor_session_id(message.session_key, workspace_id)
        claude_session_id = self._ensure_agent_session_id(message.session_key, workspace_id, "claude")
        codex_session_id = self._ensure_agent_session_id(message.session_key, workspace_id, "codex")
        state = self._load_session(message.session_key, workspace_id)
        if state is None:
            return AdapterResponse(text=f"Workspace: {workspace_id}\nNo active task in this workspace yet. Use /write <task> first.")
        task_id = state.root_task_id
        memory_key = self._memory_key(message.session_key, workspace_id)
        discussion_brief = self._build_discussion_brief(
            session_key=message.session_key,
            workspace_id=workspace_id,
            state=state,
        )
        self.store.append_task_handoff(
            session_key=message.session_key,
            workspace_id=workspace_id,
            task_id=task_id,
            handoff_type="discussion_brief",
            source_agent="claude",
            target_agent="codex",
            content_json=SessionStore.dumps_json(discussion_brief),
        )
        execution_packet = self._build_execution_packet(
            session_key=message.session_key,
            workspace_id=workspace_id,
            state=state,
            operator_note=argument,
            discussion_brief=discussion_brief,
        )
        self.store.append_task_handoff(
            session_key=message.session_key,
            workspace_id=workspace_id,
            task_id=task_id,
            handoff_type="execution_packet",
            source_agent="claude",
            target_agent="codex",
            content_json=SessionStore.dumps_json(execution_packet),
        )
        self.store.upsert_workspace_session(
            session_key=message.session_key,
            workspace_id=workspace_id,
            active_task_id=task_id,
            executor_session_id=executor_session_id,
            claude_session_id=claude_session_id,
            codex_session_id=codex_session_id,
            phase=PHASE_EXECUTING,
            conversation_summary=self.coordinator.render_remote_summary(state),
            swarm_state_json=self._serialize_state(state),
            escalations_json=self._serialize_events(self.escalations.get(memory_key, [])),
        )
        self.store.append_message(session_key=memory_key, task_id=task_id, role="system", text=f"Execution packet prepared.\n{execution_packet}")
        self.store.append_agent_message(
            session_key=message.session_key,
            workspace_id=workspace_id,
            agent="codex",
            task_id=task_id,
            role="system",
            text=json.dumps(execution_packet, ensure_ascii=False),
        )
        try:
            codex_result = self._run_agent_prompt(
                session_key=message.session_key,
                workspace_id=workspace_id,
                phase=PHASE_EXECUTING,
                mode="codex",
                prompt=json.dumps(execution_packet, ensure_ascii=False, indent=2),
            )
            self.store.append_message(session_key=memory_key, task_id=task_id, role="assistant", text=f"[codex]\n{codex_result.output}")
            self.store.append_agent_message(
                session_key=message.session_key,
                workspace_id=workspace_id,
                agent="codex",
                task_id=task_id,
                role="assistant",
                text=codex_result.output,
            )
            review_result = self._run_agent_prompt(
                session_key=message.session_key,
                workspace_id=workspace_id,
                phase=PHASE_REVIEWING,
                mode="claude",
                prompt=self._build_review_prompt(
                    state=state,
                    discussion_brief=discussion_brief,
                    execution_packet=execution_packet,
                    codex_output=codex_result.output,
                ),
            )
            text = (
                f"Task ID: {task_id}\n"
                f"Phase: {PHASE_REPORTED}\n"
                f"Execution Backend: {codex_result.backend}\n"
                f"Report Backend: {review_result.backend}\n"
                f"{review_result.output}"
            )
            self.store.append_message(session_key=memory_key, task_id=task_id, role="assistant", text=text)
            self.store.append_agent_message(
                session_key=message.session_key,
                workspace_id=workspace_id,
                agent="claude",
                task_id=task_id,
                role="system",
                text=self._build_review_prompt(
                    state=state,
                    discussion_brief=discussion_brief,
                    execution_packet=execution_packet,
                    codex_output=codex_result.output,
                ),
            )
            self.store.append_agent_message(
                session_key=message.session_key,
                workspace_id=workspace_id,
                agent="claude",
                task_id=task_id,
                role="assistant",
                text=text,
            )
            review_verdict = self._build_review_verdict(
                state=state,
                discussion_brief=discussion_brief,
                execution_packet=execution_packet,
                codex_output=codex_result.output,
                final_report=text,
            )
            self.store.append_task_handoff(
                session_key=message.session_key,
                workspace_id=workspace_id,
                task_id=task_id,
                handoff_type="review_verdict",
                source_agent="claude",
                target_agent="user",
                content_json=SessionStore.dumps_json(review_verdict),
            )
            self._persist_session(
                message,
                workspace_id,
                state,
                executor_session_id=executor_session_id,
                phase=PHASE_REPORTED,
            )
            return AdapterResponse(text=text, task_id=task_id)
        except ExecutorError as exc:
            self._persist_session(
                message,
                workspace_id,
                state,
                executor_session_id=executor_session_id,
                phase=PHASE_READY,
            )
            return AdapterResponse(text=f"Task ID: {task_id}\nPhase: {PHASE_READY}\nExecution error: {exc}", task_id=task_id)

    def _handle_new(self, message: RemoteMessage) -> AdapterResponse:
        workspace_id = self._get_or_create_bound_workspace(message)
        memory_key = self._memory_key(message.session_key, workspace_id)
        self.sessions.pop(memory_key, None)
        self.escalations.pop(memory_key, None)
        self.store.clear_workspace_session(message.session_key, workspace_id)
        return AdapterResponse(text=f"Started a fresh task context in workspace `{workspace_id}`. Use /write <task> to begin.")

    def _handle_status(self, message: RemoteMessage) -> AdapterResponse:
        workspace_id = self._get_or_create_bound_workspace(message)
        executor_session_id = self._ensure_executor_session_id(message.session_key, workspace_id)
        claude_session_id = self._ensure_agent_session_id(message.session_key, workspace_id, "claude")
        codex_session_id = self._ensure_agent_session_id(message.session_key, workspace_id, "codex")
        state = self._load_session(message.session_key, workspace_id)
        phase = self._current_phase(message.session_key, workspace_id)
        if state is None:
            return AdapterResponse(
                text=(
                    f"Workspace: {workspace_id}\n"
                    f"Executor Session: {executor_session_id}\n"
                    f"Claude Session: {claude_session_id}\n"
                    f"Codex Session: {codex_session_id}\n"
                    f"Phase: {phase}\n"
                    f"{self._project_context_text(workspace_id)}"
                    "No active task in this workspace yet. Use /write <task> first."
                )
            )
        session_record = self.store.get_workspace_session(message.session_key, workspace_id)
        summary = session_record.conversation_summary if session_record and session_record.conversation_summary else self.coordinator.render_remote_summary(state)
        return AdapterResponse(
            text=(
                f"Workspace: {workspace_id}\n"
                f"Executor Session: {executor_session_id}\n"
                f"Claude Session: {claude_session_id}\n"
                f"Codex Session: {codex_session_id}\n"
                f"Phase: {phase}\n"
                f"{self._project_context_text(workspace_id)}"
                f"Task ID: {state.root_task_id}\n"
                f"{summary}"
            ),
            task_id=state.root_task_id,
        )

    def _handle_sessions(self, message: RemoteMessage) -> AdapterResponse:
        workspace_id = self._get_or_create_bound_workspace(message)
        session_record = self.store.get_workspace_session(message.session_key, workspace_id)
        phase = self._current_phase(message.session_key, workspace_id)
        claude_session_id = self._ensure_agent_session_id(message.session_key, workspace_id, "claude")
        codex_session_id = self._ensure_agent_session_id(message.session_key, workspace_id, "codex")
        claude_rows = self.store.list_recent_agent_messages(message.session_key, workspace_id, "claude", limit=50)
        codex_rows = self.store.list_recent_agent_messages(message.session_key, workspace_id, "codex", limit=50)
        claude_ephemeral = self.store.list_ephemeral_messages(message.session_key, workspace_id, "claude", limit=50)
        codex_ephemeral = self.store.list_ephemeral_messages(message.session_key, workspace_id, "codex", limit=50)
        lines = [
            f"Workspace: {workspace_id}",
            f"Phase: {phase}",
            f"Active Task: {session_record.active_task_id if session_record and session_record.active_task_id else 'none'}",
            f"Claude Session: {claude_session_id}",
            f"Codex Session: {codex_session_id}",
            f"Claude Formal Messages: {len(claude_rows)}",
            f"Claude Ephemeral Messages: {len(claude_ephemeral)}",
            f"Codex Formal Messages: {len(codex_rows)}",
            f"Codex Ephemeral Messages: {len(codex_ephemeral)}",
        ]
        return AdapterResponse(text="\n".join(lines), task_id=session_record.active_task_id if session_record else None)

    def _handle_escalations(self, message: RemoteMessage) -> AdapterResponse:
        workspace_id = self._get_or_create_bound_workspace(message)
        state = self._load_session(message.session_key, workspace_id)
        if state is None:
            return AdapterResponse(text=f"Workspace: {workspace_id}\nNo active task in this workspace yet. Use /write <task> first.")
        escalated = self.escalations.get(self._memory_key(message.session_key, workspace_id), [])
        if not escalated:
            return AdapterResponse(
                text=f"Workspace: {workspace_id}\nTask ID: {state.root_task_id}\nNo escalations so far.",
                task_id=state.root_task_id,
            )
        lines = [f"Workspace: {workspace_id}", f"Task ID: {state.root_task_id}", "Escalations:"]
        lines.extend(f"- [{event.role}] {event.summary}" for event in escalated[-5:])
        return AdapterResponse(text="\n".join(lines), task_id=state.root_task_id)

    def _handle_projects(self, message: RemoteMessage, workspace_id: str) -> AdapterResponse:
        workspaces = self.store.list_workspaces()
        lines = ["Available workspaces:"]
        for workspace in workspaces:
            marker = "*" if workspace.workspace_id == workspace_id else "-"
            lines.append(f"{marker} {workspace.workspace_id} ({workspace.backend}/{workspace.transport})")
            project = self.project_context_store.get_for_workspace_path(workspace.path)
            if project and project.profile:
                lines.append(f"  Profile: {project.profile}")
        return AdapterResponse(text="\n".join(lines))

    def _handle_use(self, message: RemoteMessage, argument: str) -> AdapterResponse:
        workspace_id = self._normalize_workspace_id(argument)
        if not workspace_id:
            return AdapterResponse(text="Usage: /use <workspace>")
        self._ensure_workspace(workspace_id)
        self.store.bind_chat(
            session_key=message.session_key,
            platform=message.platform.value,
            chat_id=message.chat_id,
            thread_id=message.thread_id,
            workspace_id=workspace_id,
        )
        self._load_session(message.session_key, workspace_id)
        return AdapterResponse(text=f"Current workspace switched to `{workspace_id}`.")

    def _handle_where(self, message: RemoteMessage, workspace_id: str) -> AdapterResponse:
        workspace = self.store.get_workspace(workspace_id)
        executor_session_id = self._ensure_executor_session_id(message.session_key, workspace_id)
        claude_session_id = self._ensure_agent_session_id(message.session_key, workspace_id, "claude")
        codex_session_id = self._ensure_agent_session_id(message.session_key, workspace_id, "codex")
        phase = self._current_phase(message.session_key, workspace_id)
        return AdapterResponse(
            text=(
                f"Current workspace: {workspace_id}\n"
                f"Path: {workspace.path if workspace else Path.cwd()}\n"
                f"Backend: {workspace.backend if workspace else self._current_backend()}\n"
                f"Transport: {workspace.transport if workspace else self._current_transport()}\n"
                f"Executor Session: {executor_session_id}\n"
                f"Claude Session: {claude_session_id}\n"
                f"Codex Session: {codex_session_id}\n"
                f"Phase: {phase}\n"
                f"{self._project_context_text(workspace_id).rstrip()}"
            )
        )

    def _handle_project(self, message: RemoteMessage, workspace_id: str, argument: str) -> AdapterResponse:
        parts = argument.split(maxsplit=1)
        if len(parts) != 2 or parts[0] not in {"set-path", "set-backend", "set-transport"}:
            return AdapterResponse(
                text=(
                    "Usage:\n"
                    "/project set-path <path>\n"
                    "/project set-backend <backend>\n"
                    "/project set-transport <transport>"
                )
            )
        action, value = parts
        workspace = self.store.get_workspace(workspace_id)
        if workspace is None:
            self._ensure_workspace(workspace_id)
            workspace = self.store.get_workspace(workspace_id)
        assert workspace is not None
        path = workspace.path
        backend = workspace.backend
        transport = workspace.transport
        if action == "set-path":
            path = self._normalize_workspace_path(value)
            if path is None:
                return AdapterResponse(text="Path must point to an existing readable directory.")
        elif action == "set-backend":
            backend = value.strip().lower()
        else:
            transport = value.strip().lower()
        self.store.upsert_workspace(
            workspace_id=workspace.workspace_id,
            title=workspace.title,
            path=path,
            backend=backend,
            transport=transport,
        )
        return self._handle_where(message, workspace_id)

    def _make_task_id(self, message: RemoteMessage, argument: str) -> str:
        digest = sha1(f"{message.session_key}:{argument}".encode("utf-8")).hexdigest()
        return digest[:12]

    @staticmethod
    def _make_executor_session_id(session_key: str, workspace_id: str) -> str:
        digest = sha1(f"{session_key}:{workspace_id}".encode("utf-8")).hexdigest()
        return f"exec-{digest[:12]}"

    def _has_active_task(self, session_key: str, workspace_id: str) -> bool:
        session_record = self.store.get_workspace_session(session_key, workspace_id)
        return bool(session_record and session_record.active_task_id)

    def _load_session(self, session_key: str, workspace_id: str) -> SwarmState | None:
        memory_key = self._memory_key(session_key, workspace_id)
        if memory_key in self.sessions:
            return self.sessions[memory_key]
        record = self.store.get_workspace_session(session_key, workspace_id)
        if record is None or not record.swarm_state_json:
            return None
        state = self._deserialize_state(record.swarm_state_json)
        self.sessions[memory_key] = state
        self.escalations[memory_key] = self._deserialize_events(record.escalations_json)
        return state

    def _persist_session(
        self,
        message: RemoteMessage,
        workspace_id: str,
        state: SwarmState,
        *,
        executor_session_id: str | None = None,
        phase: str | None = None,
    ) -> None:
        memory_key = self._memory_key(message.session_key, workspace_id)
        summary = self.coordinator.render_remote_summary(state)
        resolved_executor_session_id = executor_session_id or self._ensure_executor_session_id(message.session_key, workspace_id)
        resolved_phase = phase or self._current_phase(message.session_key, workspace_id)
        resolved_claude_session_id = self._ensure_agent_session_id(message.session_key, workspace_id, "claude")
        resolved_codex_session_id = self._ensure_agent_session_id(message.session_key, workspace_id, "codex")
        self.store.upsert_task(
            task_id=state.root_task_id,
            session_key=message.session_key,
            workspace_id=workspace_id,
            title=state.tasks[state.root_task_id].title,
            status=state.tasks[state.root_task_id].status.value,
            executor_session_id=resolved_executor_session_id,
            last_checkpoint=summary,
        )
        self.store.upsert_workspace_session(
            session_key=message.session_key,
            workspace_id=workspace_id,
            active_task_id=state.root_task_id,
            executor_session_id=resolved_executor_session_id,
            claude_session_id=resolved_claude_session_id,
            codex_session_id=resolved_codex_session_id,
            phase=resolved_phase,
            conversation_summary=summary,
            swarm_state_json=self._serialize_state(state),
            escalations_json=self._serialize_events(self.escalations.get(memory_key, [])),
        )

    def _get_or_create_bound_workspace(self, message: RemoteMessage) -> str:
        binding = self.store.get_chat_binding(message.session_key)
        if binding is not None:
            self._ensure_workspace(binding.workspace_id)
            return binding.workspace_id
        workspace_id = self._default_workspace_id()
        self._ensure_workspace(workspace_id)
        self.store.bind_chat(
            session_key=message.session_key,
            platform=message.platform.value,
            chat_id=message.chat_id,
            thread_id=message.thread_id,
            workspace_id=workspace_id,
        )
        return workspace_id

    def _ensure_workspace(self, workspace_id: str) -> None:
        if self.store.get_workspace(workspace_id) is not None:
            return
        self.store.upsert_workspace(
            workspace_id=workspace_id,
            title=workspace_id,
            path=str(Path.cwd()),
            backend=self._current_backend(),
            transport=self._current_transport(),
        )

    def _ensure_executor_session_id(self, session_key: str, workspace_id: str) -> str:
        record = self.store.get_workspace_session(session_key, workspace_id)
        if record and record.executor_session_id:
            return record.executor_session_id
        executor_session_id = self._make_executor_session_id(session_key, workspace_id)
        self.store.upsert_workspace_session(
            session_key=session_key,
            workspace_id=workspace_id,
            active_task_id=record.active_task_id if record else None,
            executor_session_id=executor_session_id,
            claude_session_id=record.claude_session_id if record else self._make_agent_session_id(session_key, workspace_id, "claude"),
            codex_session_id=record.codex_session_id if record else self._make_agent_session_id(session_key, workspace_id, "codex"),
            phase=record.phase if record else PHASE_DISCUSSION,
            conversation_summary=(
                record.conversation_summary
                if record and record.conversation_summary
                else "No active task in this workspace yet. Use /write <task> first."
            ),
            swarm_state_json=record.swarm_state_json if record else "",
            escalations_json=record.escalations_json if record else "[]",
        )
        return executor_session_id

    @staticmethod
    def _make_agent_session_id(session_key: str, workspace_id: str, agent: str) -> str:
        digest = sha1(f"{session_key}:{workspace_id}:{agent}".encode("utf-8")).hexdigest()
        return f"{agent}-{digest[:12]}"

    def _ensure_agent_session_id(self, session_key: str, workspace_id: str, agent: str) -> str:
        record = self.store.get_workspace_session(session_key, workspace_id)
        existing = None
        if record:
            existing = record.claude_session_id if agent == "claude" else record.codex_session_id
        if existing:
            return existing
        executor_session_id = record.executor_session_id if record else self._make_executor_session_id(session_key, workspace_id)
        claude_session_id = record.claude_session_id if record else None
        codex_session_id = record.codex_session_id if record else None
        if agent == "claude":
            claude_session_id = self._make_agent_session_id(session_key, workspace_id, "claude")
        else:
            codex_session_id = self._make_agent_session_id(session_key, workspace_id, "codex")
        self.store.upsert_workspace_session(
            session_key=session_key,
            workspace_id=workspace_id,
            active_task_id=record.active_task_id if record else None,
            executor_session_id=executor_session_id,
            claude_session_id=claude_session_id,
            codex_session_id=codex_session_id,
            phase=record.phase if record else PHASE_DISCUSSION,
            conversation_summary=(
                record.conversation_summary
                if record and record.conversation_summary
                else "No active task in this workspace yet. Use /write <task> first."
            ),
            swarm_state_json=record.swarm_state_json if record else "",
            escalations_json=record.escalations_json if record else "[]",
        )
        return claude_session_id if agent == "claude" else codex_session_id

    def _run_agent_prompt(self, *, session_key: str, workspace_id: str, phase: str, mode: str, prompt: str):
        workspace = self.store.get_workspace(workspace_id)
        agent_session_id = self._ensure_agent_session_id(session_key, workspace_id, mode)
        final_prompt = self._prompt_with_project_context(
            workspace_id,
            self._wrap_agent_prompt(
                workspace_id=workspace_id,
                phase=phase,
                mode=mode,
                prompt=prompt,
                agent_history=self._agent_history_text(session_key=session_key, workspace_id=workspace_id, agent=mode),
            ),
        )
        return self.worker_pool.run(
            executor_session_id=agent_session_id,
            prompt=final_prompt,
            mode=mode,
            transport=workspace.transport if workspace else self._current_transport(),
            work_dir=workspace.path if workspace else None,
            executor_override=self.executor,
            extra_env=self._ccb_env(agent_session_id=agent_session_id, work_dir=workspace.path if workspace else None),
        )

    def _project_context_text(self, workspace_id: str) -> str:
        workspace = self.store.get_workspace(workspace_id)
        project = self.project_context_store.get_for_workspace_path(workspace.path if workspace else None)
        if project is None:
            return ""
        return (
            f"Project Session: {project.project_id}\n"
            f"Project Profile: {project.profile}\n"
            f"Project Summary: {project.summary}\n"
            f"Project Provider Sessions: {project.provider_session_count}\n"
            f"Project Active Sessions: {project.active_session_count}\n"
        )

    def _prompt_with_project_context(self, workspace_id: str, prompt: str) -> str:
        workspace = self.store.get_workspace(workspace_id)
        context = self.project_context_store.build_prompt_context(workspace.path if workspace else None)
        if not context:
            return prompt
        return f"{context}\n\nCurrent User Request:\n{prompt}"

    def _current_phase(self, session_key: str, workspace_id: str) -> str:
        record = self.store.get_workspace_session(session_key, workspace_id)
        return record.phase if record and record.phase else PHASE_DISCUSSION

    def _phase_for_followup(self, session_key: str, workspace_id: str) -> str:
        phase = self._current_phase(session_key, workspace_id)
        return PHASE_DISCUSSION if phase == PHASE_REPORTED else phase

    @staticmethod
    def _wrap_agent_prompt(*, workspace_id: str, phase: str, mode: str, prompt: str, agent_history: str) -> str:
        responsibility = "discussion, decomposition, validation, and user-facing reporting" if mode == "claude" else "implementation, code changes, and execution"
        history_block = f"\n\nRecent Agent Context:\n{agent_history}" if agent_history else ""
        return (
            f"Project Workspace: {workspace_id}\n"
            f"Worker Phase: {phase}\n"
            f"Assigned Agent: {mode}\n"
            f"Agent Responsibility: {responsibility}"
            f"{history_block}\n\n"
            f"Task Input:\n{prompt}"
        )

    def _agent_history_text(self, *, session_key: str, workspace_id: str, agent: str) -> str:
        rows = self.store.list_recent_agent_messages(session_key, workspace_id, agent, limit=6)
        ephemeral_rows = self.store.list_ephemeral_messages(session_key, workspace_id, agent, limit=5)
        lines = [f"- {row['role']}: {row['text']}" for row in rows]
        lines.extend(f"- ephemeral {row['role']}: {row['text']}" for row in ephemeral_rows)
        return "\n".join(lines)

    @staticmethod
    def _ccb_env(*, agent_session_id: str, work_dir: str | None) -> dict[str, str]:
        env = {"CCB_SESSION_ID": agent_session_id}
        if work_dir:
            env["CCB_WORK_DIR"] = work_dir
            env["CCB_RUN_DIR"] = work_dir
        return env

    def _should_treat_as_ephemeral(self, message: RemoteMessage, workspace_id: str) -> bool:
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            return False
        if self._has_active_task(message.session_key, workspace_id):
            return False
        lowered = text.lower()
        if lowered in LOW_SIGNAL_TEXT:
            return True
        if len(text) <= 12:
            return True
        return False

    def _build_execution_packet(
        self,
        *,
        session_key: str,
        workspace_id: str,
        state: SwarmState,
        operator_note: str,
        discussion_brief: dict[str, Any],
    ) -> dict[str, Any]:
        memory_key = self._memory_key(session_key, workspace_id)
        recent = self.store.list_recent_messages(memory_key, limit=6)
        note = operator_note.strip() or "No extra operator note."
        return {
            "task": state.tasks[state.root_task_id].title,
            "summary": self.coordinator.render_remote_summary(state),
            "discussion_brief": discussion_brief,
            "execution_note": note,
            "recent_discussion": [f"{row['role']}: {row['text']}" for row in recent[-6:]],
            "instructions": "Implement the agreed change, run the most relevant verification you can, and report concrete results.",
        }

    def _build_discussion_brief(
        self,
        *,
        session_key: str,
        workspace_id: str,
        state: SwarmState,
    ) -> dict[str, Any]:
        rows = self.store.list_recent_agent_messages(session_key, workspace_id, "claude", limit=6)
        return {
            "task": state.tasks[state.root_task_id].title,
            "summary": self.coordinator.render_remote_summary(state),
            "recent_claude_discussion": [f"{row['role']}: {row['text']}" for row in rows],
        }

    @staticmethod
    def _build_review_verdict(
        *,
        state: SwarmState,
        discussion_brief: dict[str, Any],
        execution_packet: dict[str, Any],
        codex_output: str,
        final_report: str,
    ) -> dict[str, Any]:
        return {
            "task": state.tasks[state.root_task_id].title,
            "discussion_brief": discussion_brief,
            "execution_packet": execution_packet,
            "codex_output": codex_output,
            "final_report": final_report,
        }

    @staticmethod
    def _build_review_prompt(
        *,
        state: SwarmState,
        discussion_brief: dict[str, Any],
        execution_packet: dict[str, Any],
        codex_output: str,
    ) -> str:
        return (
            f"Task: {state.tasks[state.root_task_id].title}\n"
            f"Discussion brief:\n{json.dumps(discussion_brief, ensure_ascii=False, indent=2)}\n\n"
            f"Execution packet:\n{json.dumps(execution_packet, ensure_ascii=False, indent=2)}\n\n"
            f"Codex execution result:\n{codex_output}\n\n"
            "Review the implementation result, call out risks or missing verification, and produce the final user-facing report."
        )

    @staticmethod
    def _normalize_workspace_path(raw: str) -> str | None:
        candidate = Path((raw or "").strip()).expanduser()
        if not candidate.exists() or not candidate.is_dir():
            return None
        return str(candidate.resolve())

    @staticmethod
    def _normalize_workspace_id(raw: str) -> str:
        value = (raw or "").strip().lower().replace(" ", "-")
        keep = [ch for ch in value if ch.isalnum() or ch in {"-", "_", "."}]
        return "".join(keep)

    @staticmethod
    def _memory_key(session_key: str, workspace_id: str) -> str:
        return f"{session_key}::{workspace_id}"

    @staticmethod
    def _default_workspace_id() -> str:
        return (Path.cwd().name or "default").strip().lower().replace(" ", "-")

    @staticmethod
    def _current_backend() -> str:
        import os

        return (os.getenv("ASH_EXECUTOR") or "codex").strip() or "codex"

    @staticmethod
    def _current_transport() -> str:
        import os

        return (os.getenv("ASH_EXECUTOR_TRANSPORT") or "auto").strip() or "auto"

    @staticmethod
    def _serialize_state(state: SwarmState) -> str:
        payload: dict[str, Any] = {
            "root_task_id": state.root_task_id,
            "tasks": [
                {
                    "id": task.id,
                    "title": task.title,
                    "role": task.role,
                    "status": task.status.value,
                    "parent_id": task.parent_id,
                    "notes": list(task.notes),
                }
                for task in state.tasks.values()
            ],
            "events": [
                {
                    "type": event.type.value,
                    "task_id": event.task_id,
                    "role": event.role,
                    "summary": event.summary,
                    "details": event.details,
                }
                for event in state.events
            ],
        }
        return SessionStore.dumps_json(payload)

    @staticmethod
    def _deserialize_state(raw: str) -> SwarmState:
        from .models import EventType, Task, TaskStatus

        payload = json.loads(raw or "{}")
        tasks = {
            item["id"]: Task(
                id=item["id"],
                title=item["title"],
                role=item["role"],
                status=TaskStatus(item["status"]),
                parent_id=item.get("parent_id"),
                notes=list(item.get("notes") or []),
            )
            for item in payload.get("tasks", [])
        }
        events = [
            Event(
                type=EventType(item["type"]),
                task_id=item["task_id"],
                role=item["role"],
                summary=item["summary"],
                details=item.get("details", ""),
            )
            for item in payload.get("events", [])
        ]
        return SwarmState(root_task_id=payload["root_task_id"], tasks=tasks, events=events)

    @staticmethod
    def _serialize_events(events: list[Event]) -> str:
        payload = [
            {
                "type": event.type.value,
                "task_id": event.task_id,
                "role": event.role,
                "summary": event.summary,
                "details": event.details,
            }
            for event in events
        ]
        return SessionStore.dumps_json(payload)

    @staticmethod
    def _deserialize_events(raw: str) -> list[Event]:
        from .models import EventType

        payload = json.loads(raw or "[]")
        return [
            Event(
                type=EventType(item["type"]),
                task_id=item["task_id"],
                role=item["role"],
                summary=item["summary"],
                details=item.get("details", ""),
            )
            for item in payload
        ]
