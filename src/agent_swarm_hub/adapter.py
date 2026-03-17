from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any

from .escalation import EscalationDecision
from .executor import AuthenticationRequiredError, ConfirmationRequiredError, Executor, ExecutorError
from .models import Event, EventType
from .project_context import ProjectContextStore
from .remote import RemoteMessage, parse_remote_command
from .session_store import SessionStore
from .swarm import SwarmCoordinator, SwarmState
from .worker_session import LocalExecutorSessionPool

PHASE_DISCUSSION = "discussion"
PHASE_READY = "ready_for_execution"
PHASE_EXECUTING = "executing"
PHASE_VERIFYING = "verifying"
PHASE_REVIEWING = "reviewing"
PHASE_REPORTED = "reported"
PHASE_PLANNING = "planning"
EPHEMERAL_WORKSPACE_ID = "__ephemeral__"
LOW_SIGNAL_TEXT = {"hi", "hello", "ok", "okay", "好的", "收到", "继续", "开始"}
CODEX_FIRST_HINTS = {
    "code",
    "codex",
    "debug",
    "fix",
    "implement",
    "patch",
    "refactor",
    "script",
    "test",
    "bug",
    "代码",
    "修复",
    "实现",
    "测试",
    "脚本",
    "报错",
    "文件",
}


@dataclass(slots=True)
class AdapterResponse:
    text: str
    task_id: str | None = None
    escalation: EscalationDecision | None = None
    visible_events: list[Event] = field(default_factory=list)


@dataclass(slots=True)
class PendingConfirmation:
    message: RemoteMessage
    workspace_id: str | None
    agent: str
    kind: str
    prompt: str
    task_id: str | None = None


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
        self.pending_confirmations: dict[str, PendingConfirmation] = {}
        self.confirmation_override_keys: set[str] = set()
        self.docs_dir = Path(__file__).resolve().parents[2] / "docs"

    def handle_message(self, message: RemoteMessage) -> AdapterResponse:
        workspace_id = self._get_bound_workspace(message)
        if workspace_id is not None:
            self._ensure_workspace(workspace_id)
            self._ensure_executor_session_id(message.session_key, workspace_id)
            self._load_session(message.session_key, workspace_id)
        if self._should_treat_as_ephemeral(message, workspace_id):
            return self._handle_ephemeral(message, workspace_id or EPHEMERAL_WORKSPACE_ID)
        if workspace_id is None and self._is_plain_text(message):
            return self._handle_temporary_swarm(message)
        if workspace_id is not None and message.text.strip() and not message.text.strip().startswith("/") and self._has_active_task(message.session_key, workspace_id):
            return self._handle_continue(message, message.text.strip())
        if workspace_id is not None and self._is_plain_text(message):
            return self._handle_write(message, message.text.strip())
        command = parse_remote_command(message.text)
        if command.name == "help":
            return AdapterResponse(
                text=(
                    "项目命令:\n"
                    "/projects  查看可用项目列表\n"
                    "/use <workspace>  切换当前项目\n"
                    "/where  查看当前项目、profile、session 和 phase\n"
                    "/project set-path <path>  设置项目目录\n"
                    "/project set-backend <backend>  设置项目默认后端\n"
                    "/project set-transport <transport>  设置项目默认传输层\n\n"
                    "任务命令:\n"
                    "/write <task>  显式创建新任务并进入讨论阶段\n"
                    "/execute [notes]  基于当前讨论进入执行阶段\n"
                    "/new  清空当前项目的活跃任务上下文\n"
                    "普通文本  已绑定项目时会自动开始新任务或续聊；未绑定项目时进入 temporary swarm 或 ephemeral\n\n"
                    "监控命令:\n"
                    "/status  查看当前任务摘要\n"
                    "/worker  查看当前 worker phase、handoff、sub-agent 运行情况\n"
                    "/tasks  查看当前项目最近任务列表\n"
                    "/sessions  查看 Claude/Codex formal 与 ephemeral 消息数量\n"
                    "/escalations  查看需要人工关注的升级事件\n\n"
                    "确认命令:\n"
                    "/confirm  当 bridge 上浮确认请求时继续执行\n\n"
                    "模式说明:\n"
                    "未绑定项目时，短消息进入 ephemeral；长消息进入 temporary swarm，不写入项目长期记忆。\n"
                    "使用 /use <workspace> 后进入正式项目模式。\n\n"
                    "其他:\n"
                    "/help  查看这份说明"
                )
            )
        if command.name == "projects":
            return self._handle_projects(message, workspace_id)
        if command.name == "use":
            return self._handle_use(message, command.argument)
        if command.name == "confirm":
            return self._handle_confirm(message)
        if workspace_id is None:
            return self._handle_unbound_command(command.name)
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
        if command.name == "worker":
            return self._handle_worker(message)
        if command.name == "tasks":
            return self._handle_tasks(message)
        if command.name == "sessions":
            return self._handle_sessions(message)
        return self._handle_escalations(message)

    def publish_event(self, message: RemoteMessage, event: Event) -> AdapterResponse:
        workspace_id = self._get_bound_workspace(message)
        if workspace_id is None:
            raise KeyError(f"No project bound for {message.session_key}")
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
        workspace_id = self._require_bound_workspace(message)
        if workspace_id is None:
            return self._formal_project_required_response()
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
        except AuthenticationRequiredError as exc:
            text = self._authentication_required_text(workspace_id=workspace_id, exc=exc, task_id=task_id)
        except ConfirmationRequiredError as exc:
            self._store_pending_confirmation(message, workspace_id, exc, task_id=task_id)
            text = self._confirmation_required_text(workspace_id=workspace_id, exc=exc, task_id=task_id)
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
        self._append_ephemeral_turn(
            session_key=message.session_key,
            workspace_id=workspace_id,
            agent="claude",
            role="user",
            text=text,
        )
        return AdapterResponse(
            text=(
                f"Workspace: {workspace_id}\n"
                "This short message was kept as ephemeral context only.\n"
                "It will not enter project summary or long-term session memory."
            )
        )

    def _handle_temporary_swarm(self, message: RemoteMessage) -> AdapterResponse:
        prompt = message.text.strip()
        agent = self._select_temporary_agent(prompt)
        agent_session_id = self._make_agent_session_id(message.session_key, EPHEMERAL_WORKSPACE_ID, agent)
        self._append_ephemeral_turn(
            session_key=message.session_key,
            workspace_id=EPHEMERAL_WORKSPACE_ID,
            agent=agent,
            role="user",
            text=prompt,
        )
        wrapped_prompt = self._wrap_agent_prompt(
            workspace_id="temporary",
            phase="temporary",
            mode=agent,
            prompt=prompt,
            agent_history=self._agent_history_text(
                session_key=message.session_key,
                workspace_id=EPHEMERAL_WORKSPACE_ID,
                agent=agent,
            ),
            guidance_text=self._guidance_text(phase="temporary", mode=agent, workspace_id="temporary"),
        )
        try:
            result = self.worker_pool.run(
                executor_session_id=agent_session_id,
                prompt=wrapped_prompt,
                mode=agent,
                transport=self._current_transport(),
                work_dir=None,
                executor_override=self.executor,
                extra_env=self._bridge_extra_env(
                    session_key=message.session_key,
                    workspace_id=EPHEMERAL_WORKSPACE_ID,
                    agent_session_id=agent_session_id,
                    work_dir=None,
                ),
            )
            self._append_ephemeral_turn(
                session_key=message.session_key,
                workspace_id=EPHEMERAL_WORKSPACE_ID,
                agent=agent,
                role="assistant",
                text=result.output,
            )
            return AdapterResponse(
                text=(
                    "Temporary Swarm Mode\n"
                    f"Starting Agent: {agent}\n"
                    f"Backend: {result.backend}\n"
                    "This exchange will auto-expire and will not enter project memory.\n"
                    f"{result.output}"
                )
            )
        except AuthenticationRequiredError as exc:
            return AdapterResponse(
                text=(
                    "Temporary Swarm Mode\n"
                    f"Starting Agent: {agent}\n"
                    "This exchange will auto-expire and will not enter project memory.\n"
                    f"{self._authentication_required_text(workspace_id=None, exc=exc, task_id=None)}"
                )
            )
        except ConfirmationRequiredError as exc:
            self._store_pending_confirmation(message, None, exc)
            return AdapterResponse(
                text=self._confirmation_required_text(
                    workspace_id=None,
                    exc=exc,
                    task_id=None,
                )
            )
        except ExecutorError as exc:
            return AdapterResponse(
                text=(
                    "Temporary Swarm Mode\n"
                    f"Starting Agent: {agent}\n"
                    "This exchange will auto-expire and will not enter project memory.\n"
                    f"Execution error: {exc}"
                )
            )

    def _handle_continue(self, message: RemoteMessage, argument: str) -> AdapterResponse:
        workspace_id = self._require_bound_workspace(message)
        if workspace_id is None:
            return self._formal_project_required_response()
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
        except AuthenticationRequiredError as exc:
            text = self._authentication_required_text(workspace_id=workspace_id, exc=exc, task_id=task_id)
        except ConfirmationRequiredError as exc:
            self._store_pending_confirmation(message, workspace_id, exc, task_id=task_id)
            text = self._confirmation_required_text(workspace_id=workspace_id, exc=exc, task_id=task_id)
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
        workspace_id = self._require_bound_workspace(message)
        if workspace_id is None:
            return self._formal_project_required_response()
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
        self.coordinator.record_event(
            state,
            Event(
                type=EventType.TASK_STARTED,
                task_id=task_id,
                role="codex",
                summary="Execution started.",
            ),
        )
        execution_plan = None
        planning_backend = None
        complexity = self._task_complexity(state.tasks[state.root_task_id].title, argument)
        self.store.append_task_handoff(
            session_key=message.session_key,
            workspace_id=workspace_id,
            task_id=task_id,
            handoff_type="discussion_brief",
            source_agent="claude",
            target_agent="codex",
            content_json=SessionStore.dumps_json(discussion_brief),
        )
        if complexity != "simple":
            try:
                planning_result = self._run_agent_prompt(
                    session_key=message.session_key,
                    workspace_id=workspace_id,
                    phase=PHASE_PLANNING,
                    mode="claude",
                    prompt=self._build_planning_prompt(
                        state=state,
                        operator_note=argument,
                        discussion_brief=discussion_brief,
                        complexity=complexity,
                    ),
                )
                planning_backend = planning_result.backend
                execution_plan = self._build_execution_plan(
                    state=state,
                    discussion_brief=discussion_brief,
                    operator_note=argument,
                    complexity=complexity,
                    planner_output=planning_result.output,
                )
                self.store.append_agent_message(
                    session_key=message.session_key,
                    workspace_id=workspace_id,
                    agent="claude",
                    task_id=task_id,
                    role="assistant",
                    text=planning_result.output,
                )
                self.store.append_task_handoff(
                    session_key=message.session_key,
                    workspace_id=workspace_id,
                    task_id=task_id,
                    handoff_type="execution_plan",
                    source_agent="claude",
                    target_agent="worker",
                    content_json=SessionStore.dumps_json(execution_plan),
                )
            except ExecutorError:
                execution_plan = self._build_execution_plan(
                    state=state,
                    discussion_brief=discussion_brief,
                    operator_note=argument,
                    complexity=complexity,
                    planner_output="Planner unavailable. Proceed with direct execution.",
                )
        execution_packet = self._build_execution_packet(
            session_key=message.session_key,
            workspace_id=workspace_id,
            state=state,
            operator_note=argument,
            discussion_brief=discussion_brief,
            execution_plan=execution_plan,
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
            subagent_results = self._run_subagents(
                session_key=message.session_key,
                workspace_id=workspace_id,
                task_id=task_id,
                state=state,
                execution_plan=execution_plan,
                execution_packet=execution_packet,
            )
            if subagent_results:
                execution_packet["subagent_results"] = subagent_results
                self.store.append_agent_message(
                    session_key=message.session_key,
                    workspace_id=workspace_id,
                    agent="codex",
                    task_id=task_id,
                    role="system",
                    text=json.dumps({"subagent_results": subagent_results}, ensure_ascii=False),
                )
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

            # --- Verification Phase ---
            verification_result = self._run_verification(
                session_key=message.session_key,
                workspace_id=workspace_id,
                task_id=task_id,
                codex_output=codex_result.output,
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
                    verification_result=verification_result.output if verification_result else "Verification skipped or failed to run.",
                ),
            )
            text = "".join(
                [
                    f"Task ID: {task_id}\n",
                    f"Phase: {PHASE_REPORTED}\n",
                    f"Complexity: {complexity}\n",
                    f"Planning Backend: {planning_backend}\n" if planning_backend else "",
                    f"Sub-agent Runs: {len(subagent_results)}\n" if subagent_results else "",
                    f"Execution Backend: {codex_result.backend}\n",
                    f"Verification Backend: {verification_result.backend}\n" if verification_result else "",
                    f"Report Backend: {review_result.backend}\n",
                    f"{review_result.output}",
                ]
            )
            self.store.append_message(
                session_key=memory_key,
                task_id=task_id,
                role="system",
                text=self._build_review_prompt(
                    state=state,
                    discussion_brief=discussion_brief,
                    execution_packet=execution_packet,
                    codex_output=codex_result.output,
                    verification_result=verification_result.output if verification_result else "Verification skipped or failed to run.",
                ),
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
                    verification_result=verification_result.output if verification_result else "Verification skipped or failed to run.",
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
                verification_result=verification_result.output if verification_result else "Verification skipped or failed to run.",
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
            self.coordinator.record_event(
                state,
                Event(
                    type=EventType.TASK_COMPLETED,
                    task_id=task_id,
                    role="claude",
                    summary="Execution reviewed and reported.",
                ),
            )
            self._persist_session(
                message,
                workspace_id,
                state,
                executor_session_id=executor_session_id,
                phase=PHASE_REPORTED,
            )
            return AdapterResponse(text=text, task_id=task_id)
        except AuthenticationRequiredError as exc:
            self._persist_session(
                message,
                workspace_id,
                state,
                executor_session_id=executor_session_id,
                phase=self._current_phase(message.session_key, workspace_id),
            )
            return AdapterResponse(
                text=self._authentication_required_text(
                    workspace_id=workspace_id,
                    exc=exc,
                    task_id=task_id,
                ),
                task_id=task_id,
            )
        except ConfirmationRequiredError as exc:
            self._persist_session(
                message,
                workspace_id,
                state,
                executor_session_id=executor_session_id,
                phase=self._current_phase(message.session_key, workspace_id),
            )
            self._store_pending_confirmation(message, workspace_id, exc, task_id=task_id)
            return AdapterResponse(
                text=self._confirmation_required_text(
                    workspace_id=workspace_id,
                    exc=exc,
                    task_id=task_id,
                ),
                task_id=task_id,
            )
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
        workspace_id = self._require_bound_workspace(message)
        if workspace_id is None:
            return self._formal_project_required_response()
        self._sync_project_memory(session_key=message.session_key, workspace_id=workspace_id)
        memory_key = self._memory_key(message.session_key, workspace_id)
        self.sessions.pop(memory_key, None)
        self.escalations.pop(memory_key, None)
        self.pending_confirmations.pop(memory_key, None)
        self.confirmation_override_keys.discard(memory_key)
        self.store.clear_workspace_session(message.session_key, workspace_id)
        return AdapterResponse(text=f"Started a fresh task context in workspace `{workspace_id}`. Send a normal message or use /write <task> to begin.")

    def _handle_status(self, message: RemoteMessage) -> AdapterResponse:
        workspace_id = self._require_bound_workspace(message)
        if workspace_id is None:
            return self._formal_project_required_response()
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
                    "No active task in this workspace yet. Send a normal message or use /write <task> to begin."
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

    def _handle_worker(self, message: RemoteMessage) -> AdapterResponse:
        workspace_id = self._require_bound_workspace(message)
        if workspace_id is None:
            return self._formal_project_required_response()
        session_record = self.store.get_workspace_session(message.session_key, workspace_id)
        phase = self._current_phase(message.session_key, workspace_id)
        task_id = session_record.active_task_id if session_record and session_record.active_task_id else None
        handoffs = self.store.list_task_handoffs(message.session_key, workspace_id, task_id, limit=8) if task_id else []
        subagent_runs = [row for row in handoffs if row["handoff_type"] == "subagent_result"]
        lines = [
            f"Workspace: {workspace_id}",
            f"Phase: {phase}",
            f"Active Task: {task_id or 'none'}",
            f"Claude Session: {session_record.claude_session_id if session_record else 'none'}",
            f"Codex Session: {session_record.codex_session_id if session_record else 'none'}",
            f"Recent Handoffs: {len(handoffs)}",
            f"Sub-agent Runs: {len(subagent_runs)}",
        ]
        if handoffs:
            lines.append("Latest Handoffs:")
            for row in handoffs[-5:]:
                lines.append(f"- {row['handoff_type']} {row['source_agent']} -> {row['target_agent']}")
        pending = self._get_pending_confirmation(message.session_key, workspace_id)
        if pending is not None:
            lines.append("Confirmation Pending:")
            lines.append(f"- Agent: {pending.agent}")
            lines.append(f"- Kind: {pending.kind}")
        return AdapterResponse(text="\n".join(lines), task_id=task_id)

    def _handle_tasks(self, message: RemoteMessage) -> AdapterResponse:
        workspace_id = self._require_bound_workspace(message)
        if workspace_id is None:
            return self._formal_project_required_response()
        tasks = self.store.list_tasks(message.session_key, workspace_id, limit=10)
        if not tasks:
            return AdapterResponse(text=f"Workspace: {workspace_id}\nNo tasks recorded yet.")
        lines = [f"Workspace: {workspace_id}", "Recent Tasks:"]
        for task in tasks:
            lines.append(f"- {task.task_id} [{task.status}] {task.title}")
        return AdapterResponse(text="\n".join(lines), task_id=tasks[0].task_id if tasks else None)

    def _handle_sessions(self, message: RemoteMessage) -> AdapterResponse:
        workspace_id = self._require_bound_workspace(message)
        if workspace_id is None:
            return self._formal_project_required_response()
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
        pending = self._get_pending_confirmation(message.session_key, workspace_id)
        if pending is not None:
            lines.append("Confirmation Pending: yes")
            lines.append(f"Pending Agent: {pending.agent}")
        return AdapterResponse(text="\n".join(lines), task_id=session_record.active_task_id if session_record else None)

    def _handle_escalations(self, message: RemoteMessage) -> AdapterResponse:
        workspace_id = self._require_bound_workspace(message)
        if workspace_id is None:
            return self._formal_project_required_response()
        state = self._load_session(message.session_key, workspace_id)
        if state is None:
            return AdapterResponse(text=f"Workspace: {workspace_id}\nNo active task in this workspace yet. Send a normal message or use /write <task> to begin.")
        escalated = self.escalations.get(self._memory_key(message.session_key, workspace_id), [])
        if not escalated:
            return AdapterResponse(
                text=f"Workspace: {workspace_id}\nTask ID: {state.root_task_id}\nNo escalations so far.",
                task_id=state.root_task_id,
            )
        lines = [f"Workspace: {workspace_id}", f"Task ID: {state.root_task_id}", "Escalations:"]
        lines.extend(f"- [{event.role}] {event.summary}" for event in escalated[-5:])
        return AdapterResponse(text="\n".join(lines), task_id=state.root_task_id)

    def _handle_projects(self, message: RemoteMessage, workspace_id: str | None) -> AdapterResponse:
        local_workspaces = {workspace.workspace_id: workspace for workspace in self.store.list_workspaces()}
        shared_projects = self.project_context_store.list_projects()
        for project in shared_projects:
            local_workspaces.setdefault(
                project.project_id,
                self.store.get_workspace(project.project_id)
                or self._workspace_record_from_project(project.project_id),
            )
        lines = ["Projects:"]
        for workspace_id_key in sorted(local_workspaces):
            workspace = local_workspaces[workspace_id_key]
            marker = "*" if workspace.workspace_id == workspace_id else "-"
            lines.append(f"{marker} {workspace.workspace_id}")
            lines.append(f"  Mode: formal ({workspace.backend}/{workspace.transport})")
            project = self.project_context_store.get_project(workspace.workspace_id)
            if project is None:
                project = self.project_context_store.get_for_workspace_path(workspace.path)
            path_text = self._short_path(project.workspace_path if project and project.workspace_path else workspace.path)
            lines.append(f"  Path: {path_text or 'not set'}")
            if project and project.profile:
                lines.append(f"  Profile: {project.profile}")
                focus = self._project_focus(project.summary)
                if focus:
                    lines.append(f"  Focus: {focus}")
        temp_marker = "*" if workspace_id is None else "-"
        lines.append(f"{temp_marker} temporary")
        lines.append("  Mode: temporary")
        lines.append("  Profile: 临时对话模式，不进入项目长期记忆；切换离开时清理临时上下文。")
        if workspace_id is None:
            lines.append("No project is currently bound to this chat. Use /use <workspace> to enter formal project mode.")
        return AdapterResponse(text="\n".join(lines))

    def _handle_use(self, message: RemoteMessage, argument: str) -> AdapterResponse:
        workspace_id = self._normalize_workspace_id(argument)
        if not workspace_id:
            return AdapterResponse(text="Usage: /use <workspace>")
        current_workspace_id = self._get_bound_workspace(message)
        if current_workspace_id and current_workspace_id != workspace_id:
            self._sync_project_memory(session_key=message.session_key, workspace_id=current_workspace_id)
        if workspace_id in {"temporary", "temp"}:
            self._clear_pending_confirmations(message.session_key)
            self.store.clear_chat_binding(message.session_key)
            self.store.clear_ephemeral_messages(message.session_key, EPHEMERAL_WORKSPACE_ID)
            return AdapterResponse(
                text=(
                    "Current mode switched to `temporary`.\n"
                    "Temporary messages will not enter project memory and will be cleared when you switch away."
                )
            )
        self._clear_pending_confirmations(message.session_key)
        self.store.clear_ephemeral_messages(message.session_key, EPHEMERAL_WORKSPACE_ID)
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

    def _handle_confirm(self, message: RemoteMessage) -> AdapterResponse:
        pending = self._find_pending_confirmation(message.session_key)
        if pending is None:
            return AdapterResponse(text="No confirmation is currently pending for this chat.")
        key = self._confirmation_key(message.session_key, pending.workspace_id)
        self.pending_confirmations.pop(key, None)
        self.confirmation_override_keys.add(key)
        try:
            replay = self.handle_message(pending.message)
        finally:
            self.confirmation_override_keys.discard(key)
        if replay.text.startswith("Confirmation Required"):
            return replay
        return AdapterResponse(
            text=f"Confirmation accepted for {pending.agent}.\n{replay.text}",
            task_id=replay.task_id,
            escalation=replay.escalation,
            visible_events=replay.visible_events,
        )

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
        self._sync_project_memory(
            session_key=message.session_key,
            workspace_id=workspace_id,
            state=state,
            summary=summary,
        )

    def _get_bound_workspace(self, message: RemoteMessage) -> str | None:
        binding = self.store.get_chat_binding(message.session_key)
        if binding is not None:
            return binding.workspace_id
        return None

    def _require_bound_workspace(self, message: RemoteMessage) -> str | None:
        workspace_id = self._get_bound_workspace(message)
        if workspace_id is not None:
            self._ensure_workspace(workspace_id)
        return workspace_id

    def _ensure_workspace(self, workspace_id: str) -> None:
        if self.store.get_workspace(workspace_id) is not None:
            return
        shared_project = self.project_context_store.get_project(workspace_id)
        if shared_project is not None:
            default_backend = "claude"
            if "codex" in shared_project.project_id.casefold():
                default_backend = "codex"
            self.store.upsert_workspace(
                workspace_id=workspace_id,
                title=shared_project.title,
                path=shared_project.workspace_path or "",
                backend=default_backend,
                transport=self._current_transport(),
            )
            return
        self.store.upsert_workspace(
            workspace_id=workspace_id,
            title=workspace_id,
            path=str(Path.cwd()),
            backend=self._current_backend(),
            transport=self._current_transport(),
        )

    @staticmethod
    def _workspace_record_from_project(project_id: str):
        from .session_store import WorkspaceRecord

        default_backend = "codex" if "codex" in project_id.casefold() else "claude"
        return WorkspaceRecord(
            workspace_id=project_id,
            title=project_id,
            path="",
            backend=default_backend,
            transport="direct",
            created_at="",
            updated_at="",
        )

    @staticmethod
    def _short_path(path: str | None) -> str:
        raw = (path or "").strip()
        if not raw:
            return ""
        if len(raw) <= 72:
            return raw
        return f"...{raw[-69:]}"

    def _resolve_shared_project_id(self, workspace_id: str) -> str | None:
        shared_project = self.project_context_store.get_project(workspace_id)
        if shared_project is not None:
            return shared_project.project_id
        workspace = self.store.get_workspace(workspace_id)
        project = self.project_context_store.get_for_workspace_path(workspace.path if workspace else None)
        return project.project_id if project is not None else None

    @staticmethod
    def _summary_recent_context(summary: str | None) -> str:
        text = (summary or '').strip()
        if not text:
            return ''
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines:
            if line.startswith('Recent context:'):
                return line.removeprefix('Recent context:').strip()
        if len(lines) >= 2:
            return lines[1]
        return lines[0] if lines else ''

    def _sync_project_memory(
        self,
        *,
        session_key: str,
        workspace_id: str,
        state: SwarmState | None = None,
        summary: str | None = None,
    ) -> None:
        project_id = self._resolve_shared_project_id(workspace_id)
        if not project_id:
            return
        workspace_session = self.store.get_workspace_session(session_key, workspace_id)
        resolved_state = state or self._load_session(session_key, workspace_id)
        resolved_summary = summary or (workspace_session.conversation_summary if workspace_session else '')
        focus = ''
        if resolved_state is not None and resolved_state.root_task_id in resolved_state.tasks:
            focus = resolved_state.tasks[resolved_state.root_task_id].title.strip()
        if not focus:
            focus = self._project_focus(resolved_summary)
        recent_context = self._summary_recent_context(resolved_summary)
        memory = self.project_context_store._compact(resolved_summary, 180) if resolved_summary else ''
        memory_key = self._memory_key(session_key, workspace_id)
        recent_rows = self.store.list_recent_messages(memory_key, limit=2)
        hints = [f"{row['role']}: {row['text']}" for row in recent_rows if (row['text'] or '').strip()]
        self.project_context_store.upsert_project_memory(
            project_id,
            focus=focus,
            recent_context=recent_context,
            memory=memory,
            recent_hints=hints,
        )

    @staticmethod
    def _project_focus(summary: str | None) -> str:
        text = (summary or "").strip()
        if not text:
            return ""
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("Current focus:"):
                return stripped.removeprefix("Current focus:").strip()
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("Recent context:"):
                return stripped.removeprefix("Recent context:").strip()
        return ""

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
                else "No active task in this workspace yet. Send a normal message or use /write <task> to begin."
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
                else "No active task in this workspace yet. Send a normal message or use /write <task> to begin."
            ),
            swarm_state_json=record.swarm_state_json if record else "",
            escalations_json=record.escalations_json if record else "[]",
        )
        return claude_session_id if agent == "claude" else codex_session_id

    def _run_agent_prompt(self, *, session_key: str, workspace_id: str, phase: str, mode: str, prompt: str):
        workspace = self.store.get_workspace(workspace_id)
        agent_session_id = self._ensure_agent_session_id(session_key, workspace_id, mode)
        guidance = self._guidance_text(phase=phase, mode=mode, workspace_id=workspace_id)
        final_prompt = self._prompt_with_project_context(
            workspace_id,
            self._wrap_agent_prompt(
                workspace_id=workspace_id,
                phase=phase,
                mode=mode,
                prompt=prompt,
                agent_history=self._agent_history_text(session_key=session_key, workspace_id=workspace_id, agent=mode),
                guidance_text=guidance,
            ),
        )
        return self.worker_pool.run(
            executor_session_id=agent_session_id,
            prompt=final_prompt,
            mode=mode,
            transport=workspace.transport if workspace else self._current_transport(),
            work_dir=workspace.path if workspace else None,
            executor_override=self.executor,
            extra_env=self._bridge_extra_env(
                session_key=session_key,
                workspace_id=workspace_id,
                agent_session_id=agent_session_id,
                work_dir=workspace.path if workspace else None,
            ),
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

    def _project_memory_snapshot(self, workspace_id: str) -> dict[str, Any]:
        workspace = self.store.get_workspace(workspace_id)
        return self.project_context_store.build_memory_snapshot(workspace.path if workspace else None)

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
    def _wrap_agent_prompt(*, workspace_id: str, phase: str, mode: str, prompt: str, agent_history: str, guidance_text: str) -> str:
        responsibility = "discussion, decomposition, validation, and user-facing reporting" if mode == "claude" else "implementation, code changes, and execution"
        history_block = f"\n\nRecent Agent Context:\n{agent_history}" if agent_history else ""
        guidance_block = f"\n\nGuidance:\n{guidance_text}" if guidance_text else ""
        behavior_rules = ""
        prompt_lower = (prompt or "").lower()
        if mode == "claude" and phase in {PHASE_DISCUSSION, PHASE_PLANNING, PHASE_REVIEWING, PHASE_REPORTED}:
            behavior_rules = (
                "\n\nExecution Constraints:\n"
                "- Do not edit files.\n"
                "- Do not run tools or inspect files.\n"
                "- Respond in plain text only.\n"
                "- Keep the reply concise and task-focused.\n"
            )
        elif mode == "codex" and '"read_only": true' in prompt_lower:
            behavior_rules = (
                "\n\nExecution Constraints:\n"
                "- Treat this as a read-only task.\n"
                "- Do not edit files.\n"
                "- Do not run mutating commands.\n"
                "- Prefer concise inspection and reporting over long autonomous work.\n"
                "- Return findings promptly.\n"
            )
        return (
            f"Project Workspace: {workspace_id}\n"
            f"Worker Phase: {phase}\n"
            f"Assigned Agent: {mode}\n"
            f"Agent Responsibility: {responsibility}"
            f"{history_block}{guidance_block}{behavior_rules}\n\n"
            f"Task Input:\n{prompt}"
        )

    def _guidance_text(self, *, phase: str, mode: str, workspace_id: str) -> str:
        chunks: list[str] = []
        seen: set[str] = set()
        for name in self._guidance_doc_names(phase=phase, mode=mode, workspace_id=workspace_id):
            if name in seen:
                continue
            seen.add(name)
            text = self._read_guidance_doc(name)
            if text:
                chunks.append(text)
        return "\n\n".join(chunks)

    def _guidance_doc_names(self, *, phase: str, mode: str, workspace_id: str) -> list[str]:
        names = ["project-entry.md", "remote-shell.md"]
        if workspace_id != "temporary":
            names.append("worker-flow.md")
        if mode == "codex":
            names.append("codex-execution.md")
        if phase in {PHASE_PLANNING, PHASE_EXECUTING}:
            names.append("subagent-policy.md")
        return names

    def _read_guidance_doc(self, filename: str) -> str:
        path = self.docs_dir / filename
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8").strip()

    def _agent_history_text(self, *, session_key: str, workspace_id: str, agent: str) -> str:
        rows = self.store.list_recent_agent_messages(session_key, workspace_id, agent, limit=6)
        ephemeral_rows = self.store.list_ephemeral_messages(session_key, workspace_id, agent, limit=5)
        lines = [f"- {row['role']}: {row['text']}" for row in rows]
        lines.extend(f"- ephemeral {row['role']}: {row['text']}" for row in ephemeral_rows)
        return "\n".join(lines)

    def _bridge_extra_env(self, *, session_key: str, workspace_id: str, agent_session_id: str, work_dir: str | None) -> dict[str, str]:
        # Keep the app-level agent session id separate from the bridge's own
        # session resolution. CCB_SESSION_ID is reserved for real bridge session ids.
        env = {"ASH_AGENT_SESSION_ID": agent_session_id}
        if work_dir:
            env["CCB_WORK_DIR"] = work_dir
            env["CCB_RUN_DIR"] = work_dir
        if self._confirmation_key(session_key, workspace_id) in self.confirmation_override_keys:
            env["CCB_CLAUDE_CONFIRMATION_MODE"] = "auto"
        return env

    def _should_treat_as_ephemeral(self, message: RemoteMessage, workspace_id: str | None) -> bool:
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            return False
        if workspace_id is not None and self._has_active_task(message.session_key, workspace_id):
            return False
        lowered = text.lower()
        if lowered in LOW_SIGNAL_TEXT:
            return True
        if len(text) <= 12:
            return True
        return False

    @staticmethod
    def _is_plain_text(message: RemoteMessage) -> bool:
        text = (message.text or "").strip()
        return bool(text) and not text.startswith("/")

    @staticmethod
    def _select_temporary_agent(text: str) -> str:
        lowered = (text or "").lower()
        if any(hint in lowered for hint in CODEX_FIRST_HINTS):
            return "codex"
        return "claude"

    def _append_ephemeral_turn(
        self,
        *,
        session_key: str,
        workspace_id: str,
        agent: str,
        role: str,
        text: str,
    ) -> None:
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        self.store.append_ephemeral_message(
            session_key=session_key,
            workspace_id=workspace_id,
            agent=agent,
            role=role,
            text=text,
            expires_at=expires_at,
        )
        self.store.trim_ephemeral_messages(session_key, workspace_id, agent, keep=5)

    @staticmethod
    def _formal_project_required_response() -> AdapterResponse:
        return AdapterResponse(
            text=(
                "No project is currently bound to this chat.\n"
                "Formal tasks must belong to a specific project.\n"
                "Use /projects to inspect projects, then /use <workspace> before /write, /execute, or status commands."
            )
        )

    def _handle_unbound_command(self, command_name: str) -> AdapterResponse:
        if command_name in {"write", "execute", "new", "status", "where", "project", "sessions", "escalations", "worker", "tasks"}:
            return self._formal_project_required_response()
        if command_name == "confirm":
            return AdapterResponse(text="No confirmation is currently pending for this chat.")
        return AdapterResponse(
            text=(
                "This chat is currently in temporary mode only.\n"
                "Short low-signal messages will be kept as ephemeral context and auto-expired.\n"
                "Use /use <workspace> to move into a formal project."
            )
        )

    def _build_execution_packet(
        self,
        *,
        session_key: str,
        workspace_id: str,
        state: SwarmState,
        operator_note: str,
        discussion_brief: dict[str, Any],
        execution_plan: dict[str, Any] | None,
    ) -> dict[str, Any]:
        memory_key = self._memory_key(session_key, workspace_id)
        recent = self.store.list_recent_messages(memory_key, limit=6)
        note = operator_note.strip() or "No extra operator note."
        read_only = self._is_read_only_task(state.tasks[state.root_task_id].title, note)
        packet = {
            "task": state.tasks[state.root_task_id].title,
            "summary": self.coordinator.render_remote_summary(state),
            "discussion_brief": discussion_brief,
            "execution_note": note,
            "recent_discussion": [f"{row['role']}: {row['text']}" for row in recent[-6:]],
            "instructions": (
                "Inspect the repository, self-validate your reasoning, and report concrete results without editing files or running mutating commands."
                if read_only
                else "Implement the agreed change, self-validate while executing, run the most relevant verification you can, and report concrete results."
            ),
            "read_only": read_only,
        }
        if execution_plan is not None:
            packet["execution_plan"] = execution_plan
        return packet

    @staticmethod
    def _task_complexity(task_title: str, operator_note: str) -> str:
        text = f"{task_title}\n{operator_note}".lower()
        markers = (
            "sub-agent",
            "subagent",
            "多agent",
            "multi-agent",
            "swarm",
            "重构",
            "architecture",
            "refactor",
            "拆分",
            "设计",
            "规划",
            "系统",
        )
        if len(text) > 180 or any(marker in text for marker in markers):
            return "large"
        if len(text) > 80:
            return "medium"
        return "simple"

    @staticmethod
    def _suggest_subagents(complexity: str, task_title: str, operator_note: str) -> list[str]:
        text = f"{task_title}\n{operator_note}".lower()
        if complexity != "large":
            return []
        suggestions: list[str] = []
        if any(item in text for item in {"test", "验证", "测试"}):
            suggestions.append("isolated-test")
        if any(item in text for item in {"docs", "文档", "总结"}):
            suggestions.append("isolated-docs")
        suggestions.append("isolated-implementation")
        return suggestions

    @staticmethod
    def _is_read_only_task(task_title: str, operator_note: str) -> bool:
        text = f"{task_title}\n{operator_note}".lower()
        markers = (
            "do not edit files",
            "do not modify files",
            "inspect and report only",
            "read-only",
            "readonly",
            "不要改文件",
            "不要修改文件",
            "只检查",
            "只汇报",
        )
        return any(marker in text for marker in markers)

    def _build_execution_plan(
        self,
        *,
        state: SwarmState,
        discussion_brief: dict[str, Any],
        operator_note: str,
        complexity: str,
        planner_output: str,
    ) -> dict[str, Any]:
        task_title = state.tasks[state.root_task_id].title
        return {
            "task": task_title,
            "complexity": complexity,
            "needs_subagents": complexity == "large",
            "suggested_subagents": self._suggest_subagents(complexity, task_title, operator_note),
            "operator_note": operator_note.strip() or "No extra operator note.",
            "discussion_brief": discussion_brief,
            "planner_output": planner_output,
            "subagent_policy": "Only spawn sub-agents when a large task needs context isolation. Sub-agents are not organizational roles.",
        }

    def _run_subagents(
        self,
        *,
        session_key: str,
        workspace_id: str,
        task_id: str,
        state: SwarmState,
        execution_plan: dict[str, Any] | None,
        execution_packet: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not execution_plan or not execution_plan.get("needs_subagents"):
            return []
        results: list[dict[str, Any]] = []
        for role in execution_plan.get("suggested_subagents", []):
            packet = self._build_subagent_packet(
                state=state,
                role=role,
                execution_plan=execution_plan,
                execution_packet=execution_packet,
                project_memory=self._project_memory_snapshot(workspace_id),
            )
            backend_mode = self._subagent_backend_mode(role)
            self.store.append_task_handoff(
                session_key=session_key,
                workspace_id=workspace_id,
                task_id=task_id,
                handoff_type="subagent_packet",
                source_agent="worker",
                target_agent=role,
                content_json=SessionStore.dumps_json(packet),
            )
            try:
                prompt = self._wrap_agent_prompt(
                    workspace_id=workspace_id,
                    phase=PHASE_EXECUTING,
                    mode=backend_mode,
                    prompt=json.dumps(packet, ensure_ascii=False, indent=2),
                    agent_history="",
                    guidance_text=self._guidance_text(phase=PHASE_EXECUTING, mode=backend_mode, workspace_id=workspace_id),
                )
                agent_session_id = self._make_agent_session_id(session_key, workspace_id, role)
                result = self.worker_pool.run(
                    executor_session_id=agent_session_id,
                    prompt=self._prompt_with_project_context(workspace_id, prompt),
                    mode=backend_mode,
                    transport=self.store.get_workspace(workspace_id).transport if self.store.get_workspace(workspace_id) else self._current_transport(),
                    work_dir=self.store.get_workspace(workspace_id).path if self.store.get_workspace(workspace_id) else None,
                    executor_override=self.executor,
                    extra_env=self._bridge_extra_env(
                        session_key=session_key,
                        workspace_id=workspace_id,
                        agent_session_id=agent_session_id,
                        work_dir=self.store.get_workspace(workspace_id).path if self.store.get_workspace(workspace_id) else None,
                    ),
                )
                item = {
                    "role": role,
                    "mode": backend_mode,
                    "backend": result.backend,
                    "output": result.output,
                }
                results.append(item)
                self.store.append_task_handoff(
                    session_key=session_key,
                    workspace_id=workspace_id,
                    task_id=task_id,
                    handoff_type="subagent_result",
                    source_agent=role,
                    target_agent="worker",
                    content_json=SessionStore.dumps_json(item),
                )
            except ExecutorError as exc:
                item = {
                    "role": role,
                    "mode": backend_mode,
                    "backend": "error",
                    "output": f"Execution error: {exc}",
                }
                results.append(item)
                self.store.append_task_handoff(
                    session_key=session_key,
                    workspace_id=workspace_id,
                    task_id=task_id,
                    handoff_type="subagent_result",
                    source_agent=role,
                    target_agent="worker",
                    content_json=SessionStore.dumps_json(item),
                )
        return results

    def _run_verification(
        self,
        *,
        session_key: str,
        workspace_id: str,
        task_id: str,
        codex_output: str,
    ):
        packet = {
            "task_id": task_id,
            "codex_output": codex_output,
            "instructions": (
                "1. Identify project root and current changes.\n"
                "2. Create a temporary worktree under .ccb/sessions/<session_id>/verification/<task_id>.\n"
                "3. Apply the changes from codex_output to the worktree.\n"
                "4. Run verification commands (pytest, npm test, etc.) as appropriate for the project.\n"
                "5. Capture output and exit code.\n"
                "6. Clean up the worktree.\n"
                "7. Report verification success/failure with logs."
            ),
        }
        self.store.append_task_handoff(
            session_key=session_key,
            workspace_id=workspace_id,
            task_id=task_id,
            handoff_type="verification_packet",
            source_agent="worker",
            target_agent="codex",
            content_json=SessionStore.dumps_json(packet),
        )
        try:
            result = self._run_agent_prompt(
                session_key=session_key,
                workspace_id=workspace_id,
                phase=PHASE_VERIFYING,
                mode="codex",
                prompt=json.dumps(packet, ensure_ascii=False, indent=2),
            )
            item = {
                "backend": result.backend,
                "output": result.output,
            }
            self.store.append_task_handoff(
                session_key=session_key,
                workspace_id=workspace_id,
                task_id=task_id,
                handoff_type="verification_result",
                source_agent="codex",
                target_agent="worker",
                content_json=SessionStore.dumps_json(item),
            )
            return result
        except ExecutorError as exc:
            return None

    @staticmethod
    def _subagent_backend_mode(role: str) -> str:
        return "codex"

    @staticmethod
    def _build_subagent_packet(
        *,
        state: SwarmState,
        role: str,
        execution_plan: dict[str, Any],
        execution_packet: dict[str, Any],
        project_memory: dict[str, Any],
    ) -> dict[str, Any]:
        read_only = bool(execution_packet.get("read_only"))
        return {
            "task": state.tasks[state.root_task_id].title,
            "subagent_role": role,
            "project_memory": project_memory,
            "execution_plan": execution_plan,
            "execution_packet": execution_packet,
            "instructions": (
                "Use this isolated execution context to inspect only your specialized slice of the task. Do not edit files or run mutating commands. Return concise findings to the main worker."
                if read_only
                else "Use this isolated execution context to complete only your specialized slice of the task, then return concrete findings to the main worker."
            ),
            "read_only": read_only,
        }

    @staticmethod
    def _build_planning_prompt(
        *,
        state: SwarmState,
        operator_note: str,
        discussion_brief: dict[str, Any],
        complexity: str,
    ) -> str:
        return (
            f"Task: {state.tasks[state.root_task_id].title}\n"
            f"Estimated complexity: {complexity}\n"
            f"Operator note: {operator_note.strip() or 'No extra operator note.'}\n"
            f"Discussion brief:\n{json.dumps(discussion_brief, ensure_ascii=False, indent=2)}\n\n"
            "Produce a concise execution plan. Only suggest sub-agents when context isolation is needed. Treat sub-agents as isolated execution contexts, not organizational roles. Outline the main execution steps."
        )

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
        verification_result: str,
        final_report: str,
    ) -> dict[str, Any]:
        return {
            "task": state.tasks[state.root_task_id].title,
            "discussion_brief": discussion_brief,
            "execution_packet": execution_packet,
            "codex_output": codex_output,
            "verification_result": verification_result,
            "final_report": final_report,
        }

    @staticmethod
    def _build_review_prompt(
        *,
        state: SwarmState,
        discussion_brief: dict[str, Any],
        execution_packet: dict[str, Any],
        codex_output: str,
        verification_result: str,
    ) -> str:
        return (
            f"Task: {state.tasks[state.root_task_id].title}\n"
            f"Discussion brief:\n{json.dumps(discussion_brief, ensure_ascii=False, indent=2)}\n\n"
            f"Execution packet:\n{json.dumps(execution_packet, ensure_ascii=False, indent=2)}\n\n"
            f"Codex execution result:\n{codex_output}\n\n"
            f"Verification result:\n{verification_result}\n\n"
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

    def _confirmation_key(self, session_key: str, workspace_id: str | None) -> str:
        return self._memory_key(session_key, workspace_id or EPHEMERAL_WORKSPACE_ID)

    def _store_pending_confirmation(
        self,
        message: RemoteMessage,
        workspace_id: str | None,
        exc: ConfirmationRequiredError,
        *,
        task_id: str | None = None,
    ) -> None:
        self.pending_confirmations[self._confirmation_key(message.session_key, workspace_id)] = PendingConfirmation(
            message=message,
            workspace_id=workspace_id,
            agent=exc.agent,
            kind=exc.kind,
            prompt=exc.prompt,
            task_id=task_id,
        )

    def _get_pending_confirmation(self, session_key: str, workspace_id: str | None) -> PendingConfirmation | None:
        return self.pending_confirmations.get(self._confirmation_key(session_key, workspace_id))

    def _find_pending_confirmation(self, session_key: str) -> PendingConfirmation | None:
        binding = self.store.get_chat_binding(session_key)
        if binding is not None:
            pending = self._get_pending_confirmation(session_key, binding.workspace_id)
            if pending is not None:
                return pending
        return self._get_pending_confirmation(session_key, None)

    def _clear_pending_confirmations(self, session_key: str) -> None:
        prefix = f"{session_key}::"
        for key in [item for item in self.pending_confirmations if item.startswith(prefix)]:
            self.pending_confirmations.pop(key, None)
            self.confirmation_override_keys.discard(key)

    @staticmethod
    def _confirmation_required_text(
        *,
        workspace_id: str | None,
        exc: ConfirmationRequiredError,
        task_id: str | None,
    ) -> str:
        lines = ["Confirmation Required"]
        if workspace_id:
            lines.append(f"Workspace: {workspace_id}")
        if task_id:
            lines.append(f"Task ID: {task_id}")
        lines.append(f"Agent: {exc.agent}")
        if exc.kind:
            lines.append(f"Kind: {exc.kind}")
        lines.append(exc.prompt)
        lines.append("Use /confirm to continue.")
        return "\n".join(lines)

    @staticmethod
    def _authentication_required_text(
        *,
        workspace_id: str | None,
        exc: AuthenticationRequiredError,
        task_id: str | None,
    ) -> str:
        lines = ["Authentication Required"]
        if workspace_id:
            lines.append(f"Workspace: {workspace_id}")
        if task_id:
            lines.append(f"Task ID: {task_id}")
        lines.append(f"Agent: {exc.agent}")
        lines.append(exc.prompt)
        lines.append("Finish provider sign-in in the local bridge session, then retry the request.")
        return "\n".join(lines)

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
