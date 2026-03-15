from pathlib import Path
import sqlite3

from agent_swarm_hub import (
    CCConnectAdapter,
    EchoExecutor,
    Event,
    EventType,
    RemoteMessage,
    RemotePlatform,
    SessionStore,
    parse_remote_command,
)


def _init_project_session_db(db_path: Path, workspace_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (
                project_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                workspace_path TEXT NOT NULL,
                profile TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL
            );
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "sheep-gwas",
                "sheep-gwas",
                workspace_path,
                "Sheep GWAS analysis workspace for plotting, QC, and result interpretation.",
                "Project: sheep-gwas\nCurrent focus: GWAS plotting",
            ),
        )
        conn.executemany(
            """
            INSERT INTO provider_sessions (provider, raw_session_id, project_id)
            VALUES (?, ?, ?)
            """,
            [("claude", "c1", "sheep-gwas"), ("codex", "x1", "sheep-gwas")],
        )


def test_parse_remote_command_defaults_plain_text_to_write() -> None:
    command = parse_remote_command("Draft a rollout plan")

    assert command.name == "write"
    assert command.argument == "Draft a rollout plan"


def test_parse_remote_command_supports_workspace_switch() -> None:
    command = parse_remote_command("/use sheep-gwas")

    assert command.name == "use"
    assert command.argument == "sheep-gwas"


def test_parse_remote_command_supports_project_config() -> None:
    command = parse_remote_command("/project set-backend claude")

    assert command.name == "project"
    assert command.argument == "set-backend claude"


def test_parse_remote_command_supports_execute() -> None:
    command = parse_remote_command("/execute run the tests")

    assert command.name == "execute"
    assert command.argument == "run the tests"


def test_parse_remote_command_supports_sessions() -> None:
    command = parse_remote_command("/sessions")

    assert command.name == "sessions"
    assert command.argument == ""


def test_write_creates_session_and_status_reads_it(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.sqlite3")
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=store)
    message = RemoteMessage(
        platform=RemotePlatform.TELEGRAM,
        chat_id="chat-1",
        user_id="user-1",
        text="/write Draft a Telegram rollout",
    )

    write_response = adapter.handle_message(message)
    status_response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/status",
        )
    )

    assert write_response.task_id is not None
    assert "Backend: echo" in write_response.text
    assert "Phase: discussion" in write_response.text
    assert write_response.task_id in status_response.text
    assert "Executor Session: exec-" in status_response.text
    assert "Claude Session: claude-" in status_response.text
    assert "Codex Session: codex-" in status_response.text
    assert "Phase: discussion" in status_response.text


def test_plain_text_continues_existing_task(tmp_path) -> None:
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=SessionStore(tmp_path / "sessions.sqlite3"))
    message = RemoteMessage(
        platform=RemotePlatform.TELEGRAM,
        chat_id="chat-1",
        user_id="user-1",
        text="/write Draft a Telegram rollout",
    )

    write_response = adapter.handle_message(message)
    continue_response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="Continue with delivery details",
        )
    )

    assert write_response.task_id == continue_response.task_id
    assert "Backend: echo" in continue_response.text


def test_short_plain_text_without_active_task_becomes_ephemeral(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.sqlite3")
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=store)

    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="好的",
        )
    )

    ephemerals = store.list_ephemeral_messages("telegram:chat-1:root", Path.cwd().name.lower().replace(" ", "-"), "claude")
    messages = store.list_recent_messages("telegram:chat-1:root")

    assert "ephemeral context only" in response.text
    assert len(ephemerals) == 1
    assert ephemerals[0]["text"] == "好的"
    assert messages == []


def test_sessions_reports_formal_and_ephemeral_counts(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.sqlite3")
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=store)

    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="hello",
        )
    )
    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/write Draft a Telegram rollout",
        )
    )
    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/sessions",
        )
    )

    assert "Claude Formal Messages:" in response.text
    assert "Claude Ephemeral Messages: 1" in response.text
    assert "Codex Formal Messages: 0" in response.text


def test_projects_lists_current_workspace(tmp_path) -> None:
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=SessionStore(tmp_path / "sessions.sqlite3"))
    default_workspace = Path.cwd().name.lower().replace(" ", "-")

    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/projects",
        )
    )

    assert "Available workspaces:" in response.text
    assert f"* {default_workspace}" in response.text


def test_projects_includes_shared_project_profile(tmp_path, monkeypatch) -> None:
    workspace_dir = tmp_path / "sheep-gwas"
    workspace_dir.mkdir()
    project_db = tmp_path / "project-sessions.sqlite3"
    _init_project_session_db(project_db, str(workspace_dir.resolve()))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(project_db))

    adapter = CCConnectAdapter(executor=EchoExecutor(), store=SessionStore(tmp_path / "sessions.sqlite3"))
    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/use sheep-gwas",
        )
    )
    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text=f"/project set-path {workspace_dir}",
        )
    )

    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/projects",
        )
    )

    assert "* sheep-gwas" in response.text
    assert "Profile: Sheep GWAS analysis workspace for plotting, QC, and result interpretation." in response.text


def test_use_switches_workspace_for_chat(tmp_path) -> None:
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=SessionStore(tmp_path / "sessions.sqlite3"))

    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/use sheep-gwas",
        )
    )
    where = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/where",
        )
    )

    assert "switched to `sheep-gwas`" in response.text
    assert "Current workspace: sheep-gwas" in where.text
    assert "Executor Session: exec-" in where.text
    assert "Claude Session: claude-" in where.text
    assert "Codex Session: codex-" in where.text
    assert "Phase: discussion" in where.text


def test_project_config_updates_workspace_metadata(tmp_path) -> None:
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=SessionStore(tmp_path / "sessions.sqlite3"))
    project_dir = tmp_path / "sheep-gwas"
    project_dir.mkdir()
    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/use sheep-gwas",
        )
    )

    backend_response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/project set-backend claude",
        )
    )
    transport_response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/project set-transport direct",
        )
    )
    path_response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text=f"/project set-path {project_dir}",
        )
    )

    assert "Backend: claude" in backend_response.text
    assert "Transport: direct" in transport_response.text
    assert f"Path: {project_dir.resolve()}" in path_response.text


def test_project_set_path_rejects_missing_directory(tmp_path) -> None:
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=SessionStore(tmp_path / "sessions.sqlite3"))
    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/use sheep-gwas",
        )
    )

    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/project set-path /tmp/does-not-exist-agent-swarm-hub",
        )
    )

    assert "existing readable directory" in response.text


def test_where_and_status_include_shared_project_context(tmp_path, monkeypatch) -> None:
    workspace_dir = tmp_path / "sheep-gwas"
    workspace_dir.mkdir()
    project_db = tmp_path / "project-sessions.sqlite3"
    _init_project_session_db(project_db, str(workspace_dir.resolve()))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(project_db))

    adapter = CCConnectAdapter(executor=EchoExecutor(), store=SessionStore(tmp_path / "sessions.sqlite3"))
    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/use sheep-gwas",
        )
    )
    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text=f"/project set-path {workspace_dir}",
        )
    )

    where = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/where",
        )
    )
    status = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/status",
        )
    )

    assert "Project Session: sheep-gwas" in where.text
    assert "Project Profile: Sheep GWAS analysis workspace for plotting, QC, and result interpretation." in where.text
    assert "Project Provider Sessions: 2" in where.text
    assert "Project Summary: Project: sheep-gwas" in where.text
    assert "Project Session: sheep-gwas" in status.text


def test_execute_routes_codex_then_claude_review(tmp_path) -> None:
    class RecordingExecutor(EchoExecutor):
        def __init__(self):
            self.prompts = []

        def run(self, prompt: str):
            self.prompts.append(prompt)
            return super().run(prompt)

    store = SessionStore(tmp_path / "sessions.sqlite3")
    executor = RecordingExecutor()
    adapter = CCConnectAdapter(executor=executor, store=store)
    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/write Draft the rollout plan",
        )
    )

    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/execute implement the approved plan",
        )
    )
    status = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/status",
        )
    )
    handoffs = store.list_task_handoffs("telegram:chat-1:root", Path.cwd().name.lower().replace(" ", "-"), write_task_id := response.task_id or "")

    assert len(executor.prompts) == 3
    assert "Assigned Agent: claude" in executor.prompts[0]
    assert "Assigned Agent: codex" in executor.prompts[1]
    assert "Assigned Agent: claude" in executor.prompts[2]
    assert "Phase: reported" in response.text
    assert "Execution Backend: echo" in response.text
    assert "Report Backend: echo" in response.text
    assert "Phase: reported" in status.text
    assert [row["handoff_type"] for row in handoffs] == ["discussion_brief", "execution_packet", "review_verdict"]


def test_agent_specific_history_is_injected_for_claude_followup(tmp_path) -> None:
    class RecordingExecutor(EchoExecutor):
        def __init__(self):
            self.prompts = []

        def run(self, prompt: str):
            self.prompts.append(prompt)
            return super().run(prompt)

    store = SessionStore(tmp_path / "sessions.sqlite3")
    executor = RecordingExecutor()
    adapter = CCConnectAdapter(executor=executor, store=store)

    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/write Draft the rollout plan",
        )
    )
    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="Refine the milestones",
        )
    )

    assert len(executor.prompts) == 2
    assert "Recent Agent Context:" in executor.prompts[1]
    assert "- user: Draft the rollout plan" in executor.prompts[1]

    claude_rows = store.list_recent_agent_messages("telegram:chat-1:root", Path.cwd().name.lower().replace(" ", "-"), "claude")
    codex_rows = store.list_recent_agent_messages("telegram:chat-1:root", Path.cwd().name.lower().replace(" ", "-"), "codex")

    assert len(claude_rows) >= 4
    assert codex_rows == []


def test_ephemeral_history_is_injected_into_agent_prompt(tmp_path) -> None:
    class RecordingExecutor(EchoExecutor):
        def __init__(self):
            self.prompts = []

        def run(self, prompt: str):
            self.prompts.append(prompt)
            return super().run(prompt)

    store = SessionStore(tmp_path / "sessions.sqlite3")
    executor = RecordingExecutor()
    adapter = CCConnectAdapter(executor=executor, store=store)

    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="hello",
        )
    )
    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/write Draft the rollout plan",
        )
    )

    assert "ephemeral user: hello" in executor.prompts[0]


def test_workspace_prompt_includes_shared_project_context(tmp_path, monkeypatch) -> None:
    workspace_dir = tmp_path / "sheep-gwas"
    workspace_dir.mkdir()
    project_db = tmp_path / "project-sessions.sqlite3"
    _init_project_session_db(project_db, str(workspace_dir.resolve()))
    with sqlite3.connect(project_db) as conn:
        conn.executescript(
            """
            CREATE TABLE project_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                role TEXT NOT NULL,
                text TEXT NOT NULL
            );
            """
        )
        conn.executemany(
            """
            INSERT INTO project_messages (project_id, role, text)
            VALUES (?, ?, ?)
            """,
            [
                ("sheep-gwas", "user", "Need GWAS plotting help"),
                ("sheep-gwas", "assistant", "Prior GWAS plotting suggestions"),
            ],
        )
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(project_db))

    class RecordingExecutor(EchoExecutor):
        def __init__(self):
            self.prompts = []

        def run(self, prompt: str):
            self.prompts.append(prompt)
            return super().run(prompt)

    executor = RecordingExecutor()
    adapter = CCConnectAdapter(executor=executor, store=SessionStore(tmp_path / "sessions.sqlite3"))
    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/use sheep-gwas",
        )
    )
    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text=f"/project set-path {workspace_dir}",
        )
    )

    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/write Analyze the GWAS inputs",
        )
    )

    assert executor.prompts
    assert "Project: sheep-gwas" in executor.prompts[0]
    assert "Profile: Sheep GWAS analysis workspace for plotting, QC, and result interpretation." in executor.prompts[0]
    assert "Summary: Project: sheep-gwas" in executor.prompts[0]
    assert "Assigned Agent: claude" in executor.prompts[0]
    assert "Worker Phase: discussion" in executor.prompts[0]
    assert "Task Input:\nAnalyze the GWAS inputs" in executor.prompts[0]


def test_workspace_config_controls_executor_behavior(tmp_path) -> None:
    class RecordingExecutor(EchoExecutor):
        def __init__(self, backend: str):
            self.backend = backend
            self.prompts = []

        def run(self, prompt: str):
            self.prompts.append(prompt)
            return type("Result", (), {"backend": self.backend, "output": prompt})()

    adapter = CCConnectAdapter(executor=RecordingExecutor("echo"), store=SessionStore(tmp_path / "sessions.sqlite3"))
    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/use sheep-gwas",
        )
    )
    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/write Draft a Telegram rollout",
        )
    )

    assert "Backend: echo" in response.text


def test_workspace_switch_isolates_active_tasks(tmp_path) -> None:
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=SessionStore(tmp_path / "sessions.sqlite3"))
    default_workspace = Path.cwd().name.lower().replace(" ", "-")
    first = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/write Draft a Telegram rollout",
        )
    )
    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/use sheep-gwas",
        )
    )
    empty_status = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/status",
        )
    )
    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text=f"/use {default_workspace}",
        )
    )
    restored_status = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/status",
        )
    )

    assert first.task_id is not None
    assert "No active task" in empty_status.text
    assert first.task_id in restored_status.text


def test_executor_session_id_is_stable_per_workspace_session(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.sqlite3")
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=store)
    where_response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/where",
        )
    )
    status_response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/status",
        )
    )

    record = store.get_workspace_session("telegram:chat-1:root", Path.cwd().name.lower().replace(" ", "-"))

    assert record is not None
    assert record.executor_session_id is not None
    assert record.claude_session_id is not None
    assert record.codex_session_id is not None
    assert f"Executor Session: {record.executor_session_id}" in where_response.text
    assert f"Executor Session: {record.executor_session_id}" in status_response.text
    assert f"Claude Session: {record.claude_session_id}" in where_response.text
    assert f"Codex Session: {record.codex_session_id}" in where_response.text
    assert f"Claude Session: {record.claude_session_id}" in status_response.text
    assert f"Codex Session: {record.codex_session_id}" in status_response.text

    fresh_adapter = CCConnectAdapter(executor=EchoExecutor(), store=store)
    fresh_where = fresh_adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/where",
        )
    )

    assert f"Executor Session: {record.executor_session_id}" in fresh_where.text
    assert f"Claude Session: {record.claude_session_id}" in fresh_where.text
    assert f"Codex Session: {record.codex_session_id}" in fresh_where.text


def test_workspace_session_tracks_distinct_agent_session_ids(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.sqlite3")
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=store)

    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/write Draft a Telegram rollout",
        )
    )
    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/execute implement it",
        )
    )

    record = store.get_workspace_session("telegram:chat-1:root", Path.cwd().name.lower().replace(" ", "-"))

    assert record is not None
    assert record.executor_session_id is not None
    assert record.claude_session_id is not None
    assert record.codex_session_id is not None
    assert record.claude_session_id.startswith("claude-")
    assert record.codex_session_id.startswith("codex-")
    assert record.claude_session_id != record.codex_session_id


def test_session_persists_across_adapter_instances(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.sqlite3")
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=store)
    message = RemoteMessage(
        platform=RemotePlatform.TELEGRAM,
        chat_id="chat-1",
        user_id="user-1",
        text="/write Draft a Telegram rollout",
    )

    write_response = adapter.handle_message(message)

    fresh_adapter = CCConnectAdapter(executor=EchoExecutor(), store=store)
    status_response = fresh_adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/status",
        )
    )

    assert write_response.task_id is not None
    assert write_response.task_id in status_response.text


def test_new_clears_active_task_context(tmp_path) -> None:
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=SessionStore(tmp_path / "sessions.sqlite3"))
    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/write Draft a Telegram rollout",
        )
    )

    reset_response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/new",
        )
    )
    status_response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/status",
        )
    )

    assert "fresh task context" in reset_response.text
    assert "No active task" in status_response.text


def test_blocker_event_becomes_visible_escalation() -> None:
    adapter = CCConnectAdapter(executor=EchoExecutor())
    base_message = RemoteMessage(
        platform=RemotePlatform.LARK,
        chat_id="chat-2",
        user_id="user-9",
        text="/write Prepare Lark adapter",
    )
    write_response = adapter.handle_message(base_message)
    task_id = write_response.task_id
    assert task_id is not None

    event_response = adapter.publish_event(
        RemoteMessage(
            platform=RemotePlatform.LARK,
            chat_id="chat-2",
            user_id="user-9",
            text="/status",
        ),
        Event(
            type=EventType.NEED_INPUT,
            task_id=task_id,
            role="planner",
            summary="Need bot permission scope confirmation.",
        ),
    )
    escalations_response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.LARK,
            chat_id="chat-2",
            user_id="user-9",
            text="/escalations",
        )
    )

    assert event_response.escalation is not None
    assert event_response.escalation.should_escalate is True
    assert "Need bot permission scope confirmation." in escalations_response.text
