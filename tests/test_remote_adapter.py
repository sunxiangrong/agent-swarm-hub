from pathlib import Path
import sqlite3

from agent_swarm_hub import (
    CCConnectAdapter,
    ConfirmationRequiredError,
    EchoExecutor,
    Event,
    EventType,
    RemoteMessage,
    RemotePlatform,
    SessionStore,
    parse_remote_command,
)
from agent_swarm_hub.executor import SkipPrimaryExecutor, build_executor_for_config


def _bind_workspace(adapter: CCConnectAdapter, *, workspace_id: str = "project-alpha") -> str:
    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text=f"/use {workspace_id}",
        )
    )
    assert workspace_id in response.text
    return workspace_id


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
            CREATE TABLE project_memory (
                project_id TEXT PRIMARY KEY,
                focus TEXT NOT NULL DEFAULT '',
                recent_context TEXT NOT NULL DEFAULT '',
                memory TEXT NOT NULL DEFAULT '',
                recent_hints_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE provider_bindings (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, provider)
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


def test_parse_remote_command_supports_confirm() -> None:
    command = parse_remote_command("/confirm")

    assert command.name == "confirm"
    assert command.argument == ""


def test_parse_remote_command_supports_quit() -> None:
    command = parse_remote_command("/quit")

    assert command.name == "quit"
    assert command.argument == ""


def test_parse_remote_command_supports_worker_and_tasks() -> None:
    worker = parse_remote_command("/worker")
    tasks = parse_remote_command("/tasks")

    assert worker.name == "worker"
    assert tasks.name == "tasks"


def test_parse_remote_command_supports_autostep() -> None:
    command = parse_remote_command("/autostep codex")

    assert command.name == "autostep"
    assert command.argument == "codex"


def test_parse_remote_command_supports_automonitor() -> None:
    command = parse_remote_command("/automonitor --apply --auto-continue --cycles 2")

    assert command.name == "automonitor"
    assert command.argument == "--apply --auto-continue --cycles 2"


def test_help_includes_command_explanations(tmp_path) -> None:
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=SessionStore(tmp_path / "sessions.sqlite3"))

    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/help",
        )
    )

    assert "主路径:" in response.text
    assert "/use <workspace> 进入项目" in response.text
    assert "/quit 退出当前项目模式" in response.text
    assert "/autostep [provider] [--explain]  自动推进当前项目的一小步" in response.text
    assert "/automonitor [--apply] [--auto-continue] [--until-complete] [--cycles N] [--interval N]  运行当前项目范围的有界监控循环" in response.text
    assert "/worker  查看当前 worker phase" in response.text
    assert "/confirm  当 bridge 上浮确认请求时继续执行" in response.text
    assert "远程聊天默认按自然对话使用" in response.text


def test_confirm_replays_pending_write_and_returns_result(tmp_path) -> None:
    class ConfirmingExecutor(EchoExecutor):
        def __init__(self):
            self.calls = 0

        def run(self, prompt: str):
            self.calls += 1
            if self.calls == 1:
                raise ConfirmationRequiredError(
                    prompt="Claude requires workspace trust confirmation. Use /confirm to continue.",
                    agent="claude",
                    kind="claude_trust",
                )
            return super().run(prompt)

    adapter = CCConnectAdapter(executor=ConfirmingExecutor(), store=SessionStore(tmp_path / "sessions.sqlite3"))
    _bind_workspace(adapter)

    blocked = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/write Draft a Telegram rollout",
        )
    )
    confirmed = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/confirm",
        )
    )

    assert "Confirmation Required" in blocked.text
    assert "Use /confirm to continue." in blocked.text
    assert "Confirmation accepted for claude." in confirmed.text
    assert "Phase: discussion" in confirmed.text
    assert "Backend: echo" in confirmed.text


def test_write_updates_project_memory(tmp_path, monkeypatch) -> None:
    session_db = tmp_path / "sessions.sqlite3"
    project_db = tmp_path / "project-sessions.sqlite3"
    workspace_dir = tmp_path / "sheep-gwas"
    workspace_dir.mkdir()
    _init_project_session_db(project_db, str(workspace_dir.resolve()))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(project_db))

    store = SessionStore(session_db)
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=store)
    _bind_workspace(adapter, workspace_id="sheep-gwas")

    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/write Draft GWAS plotting checklist",
        )
    )

    assert response.task_id is not None
    with sqlite3.connect(project_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT focus, recent_context, memory, recent_hints_json FROM project_memory WHERE project_id = ?",
            ("sheep-gwas",),
        ).fetchone()
    assert row is not None
    assert row["focus"] == "Draft GWAS plotting checklist"
    assert "Draft GWAS plotting checklist" in row["memory"]
    assert "Draft GWAS plotting checklist" in row["recent_hints_json"]


def test_write_creates_session_and_status_reads_it(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.sqlite3")
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=store)
    workspace_id = _bind_workspace(adapter)
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
    assert f"Workspace: {workspace_id}" in status_response.text


def test_project_memory_prefers_recent_progress_over_stage_line(tmp_path, monkeypatch) -> None:
    session_db = tmp_path / "sessions.sqlite3"
    project_db = tmp_path / "project-sessions.sqlite3"
    workspace_dir = tmp_path / "sheep-gwas"
    workspace_dir.mkdir()
    _init_project_session_db(project_db, str(workspace_dir.resolve()))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(project_db))

    store = SessionStore(session_db)
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=store)
    _bind_workspace(adapter, workspace_id="sheep-gwas")

    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/write Draft GWAS plotting checklist",
        )
    )

    assert response.task_id is not None
    with sqlite3.connect(project_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT focus, recent_context, memory FROM project_memory WHERE project_id = ?",
            ("sheep-gwas",),
        ).fetchone()

    assert row is not None
    assert row["focus"] == "Draft GWAS plotting checklist"
    assert row["recent_context"] == "No notable updates yet."
    assert row["recent_context"] != "Stage: pending"
    assert "Stage: pending" in row["memory"]


def test_new_flushes_project_memory_before_reset(tmp_path, monkeypatch) -> None:
    session_db = tmp_path / "sessions.sqlite3"
    project_db = tmp_path / "project-sessions.sqlite3"
    workspace_dir = tmp_path / "sheep-gwas"
    workspace_dir.mkdir()
    _init_project_session_db(project_db, str(workspace_dir.resolve()))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(project_db))

    store = SessionStore(session_db)
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=store)
    _bind_workspace(adapter, workspace_id="sheep-gwas")
    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/write Review GWAS summary outputs",
        )
    )

    cleared = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/new",
        )
    )

    assert "Started a fresh task context" in cleared.text
    with sqlite3.connect(project_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT focus, recent_context, memory FROM project_memory WHERE project_id = ?",
            ("sheep-gwas",),
        ).fetchone()
    assert row is not None
    assert row["focus"] == "Review GWAS summary outputs"
    assert row["memory"]


def test_plain_text_continues_existing_task(tmp_path) -> None:
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=SessionStore(tmp_path / "sessions.sqlite3"))
    _bind_workspace(adapter)
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


def test_autostep_runs_single_increment_for_bound_project(tmp_path, monkeypatch) -> None:
    session_db = tmp_path / "sessions.sqlite3"
    project_db = tmp_path / "project-sessions.sqlite3"
    workspace_dir = tmp_path / "sheep-gwas"
    workspace_dir.mkdir()
    _init_project_session_db(project_db, str(workspace_dir.resolve()))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(project_db))

    from agent_swarm_hub.project_context import ProjectContextStore

    project_store = ProjectContextStore(str(project_db))
    project_store.upsert_project_memory(
        "sheep-gwas",
        focus="收口 auto-step",
        recent_context="runtime health 已稳定，可以推进最小自动执行。",
        memory="Single-step auto execution should advance once and stop.",
        recent_hints=["Next: expose single-step auto-continue as a project command"],
    )

    adapter = CCConnectAdapter(executor=EchoExecutor(), store=SessionStore(session_db))
    _bind_workspace(adapter, workspace_id="sheep-gwas")

    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/autostep codex",
        )
    )

    assert "Automation Result" in response.text
    assert "Project: sheep-gwas" in response.text
    assert "Provider: codex" in response.text
    assert "Executed next step: Next: expose single-step auto-continue as a project command" in response.text
    assert "Execution output:" in response.text
    assert "Backend: echo" in response.text
    assert response.task_id is not None


def test_autostep_explain_renders_plan_without_execution(tmp_path, monkeypatch) -> None:
    session_db = tmp_path / "sessions.sqlite3"
    project_db = tmp_path / "project-sessions.sqlite3"
    workspace_dir = tmp_path / "sheep-gwas"
    workspace_dir.mkdir()
    _init_project_session_db(project_db, str(workspace_dir.resolve()))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(project_db))

    from agent_swarm_hub.project_context import ProjectContextStore

    project_store = ProjectContextStore(str(project_db))
    project_store.upsert_project_memory(
        "sheep-gwas",
        focus="收口 auto-step explain",
        recent_context="runtime health 已稳定，可以先解释自动推进计划。",
        memory="Single-step auto execution should support explain-only mode.",
        recent_hints=["Next: expose auto-continue explain mode"],
    )

    adapter = CCConnectAdapter(executor=EchoExecutor(), store=SessionStore(session_db))
    _bind_workspace(adapter, workspace_id="sheep-gwas")

    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/autostep --explain codex",
        )
    )

    assert "Automation Preview" in response.text
    assert "Project: sheep-gwas" in response.text
    assert "Provider: codex" in response.text
    assert "Planned next step: Next: expose auto-continue explain mode" in response.text
    assert "Explain only: no execution performed." in response.text
    assert "Backend: echo" not in response.text


def test_automonitor_runs_bounded_monitor_for_bound_project(tmp_path, monkeypatch) -> None:
    session_db = tmp_path / "sessions.sqlite3"
    project_db = tmp_path / "project-sessions.sqlite3"
    workspace_dir = tmp_path / "sheep-gwas"
    workspace_dir.mkdir()
    _init_project_session_db(project_db, str(workspace_dir.resolve()))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(project_db))

    captured: dict[str, object] = {}

    def fake_monitor(
        project_id: str | None,
        *,
        monitor_all: bool,
        apply: bool,
        auto_continue_enabled: bool,
        until_complete: bool,
        interval_seconds: float,
        cycles: int,
        sync_project_memory_artifacts_cb,
    ) -> int:
        captured.update(
            {
                "project_id": project_id,
                "monitor_all": monitor_all,
                "apply": apply,
                "auto_continue_enabled": auto_continue_enabled,
                "until_complete": until_complete,
                "interval_seconds": interval_seconds,
                "cycles": cycles,
                "has_sync_cb": callable(sync_project_memory_artifacts_cb),
            }
        )
        return 0

    monkeypatch.setattr("agent_swarm_hub.cli_ops.project_sessions_monitor", fake_monitor)

    adapter = CCConnectAdapter(executor=EchoExecutor(), store=SessionStore(session_db))
    _bind_workspace(adapter, workspace_id="sheep-gwas")

    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/automonitor --apply --auto-continue --until-complete --cycles 2 --interval 5",
        )
    )

    assert "Automation Monitor" in response.text
    assert "Project: sheep-gwas" in response.text
    assert "Scope: current project only" in response.text
    assert "Apply repair: yes" in response.text
    assert "Auto-continue: yes" in response.text
    assert "Until complete: yes" in response.text
    assert "Cycles: 2" in response.text
    assert "Interval: 5s" in response.text
    assert "Monitor exit code: 0" in response.text
    assert captured == {
        "project_id": "sheep-gwas",
        "monitor_all": False,
        "apply": True,
        "auto_continue_enabled": True,
        "until_complete": True,
        "interval_seconds": 5.0,
        "cycles": 2,
        "has_sync_cb": True,
    }


def test_natural_language_monitor_without_interval_asks_for_timing(tmp_path, monkeypatch) -> None:
    session_db = tmp_path / "sessions.sqlite3"
    project_db = tmp_path / "project-sessions.sqlite3"
    workspace_dir = tmp_path / "sheep-gwas"
    workspace_dir.mkdir()
    _init_project_session_db(project_db, str(workspace_dir.resolve()))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(project_db))

    adapter = CCConnectAdapter(executor=EchoExecutor(), store=SessionStore(session_db))
    _bind_workspace(adapter, workspace_id="sheep-gwas")

    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="帮我隔一段时间看看当前任务情况",
        )
    )

    assert response.text == "隔多久看一次？"


def test_natural_language_monitor_with_interval_routes_to_project_scoped_monitor(tmp_path, monkeypatch) -> None:
    session_db = tmp_path / "sessions.sqlite3"
    project_db = tmp_path / "project-sessions.sqlite3"
    workspace_dir = tmp_path / "sheep-gwas"
    workspace_dir.mkdir()
    _init_project_session_db(project_db, str(workspace_dir.resolve()))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(project_db))

    captured: dict[str, object] = {}

    def fake_monitor(
        project_id: str | None,
        *,
        monitor_all: bool,
        apply: bool,
        auto_continue_enabled: bool,
        until_complete: bool,
        interval_seconds: float,
        cycles: int,
        sync_project_memory_artifacts_cb,
    ) -> int:
        captured.update(
            {
                "project_id": project_id,
                "monitor_all": monitor_all,
                "apply": apply,
                "auto_continue_enabled": auto_continue_enabled,
                "until_complete": until_complete,
                "interval_seconds": interval_seconds,
                "cycles": cycles,
            }
        )
        return 0

    monkeypatch.setattr("agent_swarm_hub.cli_ops.project_sessions_monitor", fake_monitor)

    adapter = CCConnectAdapter(executor=EchoExecutor(), store=SessionStore(session_db))
    _bind_workspace(adapter, workspace_id="sheep-gwas")

    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="帮我每10秒看看当前任务情况",
        )
    )

    assert "Automation Monitor" in response.text
    assert "Project: sheep-gwas" in response.text
    assert "Scope: current project only" in response.text
    assert "Interval: 10s" in response.text
    assert captured == {
        "project_id": "sheep-gwas",
        "monitor_all": False,
        "apply": False,
        "auto_continue_enabled": False,
        "until_complete": False,
        "interval_seconds": 10.0,
        "cycles": 6,
    }


def test_autostep_refuses_quarantined_runtime_health(tmp_path, monkeypatch) -> None:
    session_db = tmp_path / "sessions.sqlite3"
    project_db = tmp_path / "project-sessions.sqlite3"
    workspace_dir = tmp_path / "sheep-gwas"
    workspace_dir.mkdir()
    _init_project_session_db(project_db, str(workspace_dir.resolve()))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(project_db))

    from agent_swarm_hub.project_context import ProjectContextStore

    project_store = ProjectContextStore(str(project_db))
    project_store.upsert_project_memory(
        "sheep-gwas",
        focus="收口 auto-step",
        recent_context="runtime health 当前阻塞。",
        memory="Auto execution should pause when runtime health is quarantined.",
        recent_hints=["Next: expose single-step auto-continue as a project command"],
    )
    project_store.record_runtime_health(
        "sheep-gwas",
        "codex",
        status="quarantined",
        summary="Bound codex session is quarantined and must not be auto-continued.",
        details={"session_id": "codex-bad", "issue": "unhealthy"},
    )

    adapter = CCConnectAdapter(executor=EchoExecutor(), store=SessionStore(session_db))
    _bind_workspace(adapter, workspace_id="sheep-gwas")

    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/autostep codex",
        )
    )

    assert "Auto-continue blocked by runtime health for `sheep-gwas`: quarantined" in response.text
    assert "must not be auto-continued" in response.text


def test_bound_plain_text_without_active_task_starts_task(tmp_path) -> None:
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=SessionStore(tmp_path / "sessions.sqlite3"))
    _bind_workspace(adapter)

    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="Draft a Telegram rollout",
        )
    )

    assert response.task_id is not None
    assert "Phase: discussion" in response.text
    assert "Backend: echo" in response.text


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

    ephemerals = store.list_ephemeral_messages("telegram:chat-1:root", "__ephemeral__", "claude")
    messages = store.list_recent_messages("telegram:chat-1:root")

    assert "ephemeral context only" in response.text
    assert len(ephemerals) == 1
    assert ephemerals[0]["text"] == "好的"
    assert messages == []


def test_unbound_long_plain_text_runs_temporary_swarm_with_claude_default(tmp_path) -> None:
    class RecordingExecutor(EchoExecutor):
        def __init__(self):
            self.prompts = []

        def run(self, prompt: str):
            self.prompts.append(prompt)
            return super().run(prompt)

    store = SessionStore(tmp_path / "sessions.sqlite3")
    executor = RecordingExecutor()
    adapter = CCConnectAdapter(executor=executor, store=store)

    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="Brainstorm a rollout approach for this idea",
        )
    )

    ephemerals = store.list_ephemeral_messages("telegram:chat-1:root", "__ephemeral__", "claude")

    assert "Temporary Swarm Mode" in response.text
    assert "Starting Agent: claude" in response.text
    assert executor.prompts
    assert "Assigned Agent: claude" in executor.prompts[0]
    assert len(ephemerals) == 2
    assert store.list_recent_messages("telegram:chat-1:root") == []


def test_unbound_long_plain_text_can_start_with_codex(tmp_path) -> None:
    class RecordingExecutor(EchoExecutor):
        def __init__(self):
            self.prompts = []

        def run(self, prompt: str):
            self.prompts.append(prompt)
            return super().run(prompt)

    executor = RecordingExecutor()
    adapter = CCConnectAdapter(executor=executor, store=SessionStore(tmp_path / "sessions.sqlite3"))

    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="Fix this script and run a test pass",
        )
    )

    assert "Starting Agent: codex" in response.text
    assert "Assigned Agent: codex" in executor.prompts[0]


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
    _bind_workspace(adapter)
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
    assert "Claude Ephemeral Messages: 0" in response.text
    assert "Codex Formal Messages: 0" in response.text


def test_unbound_formal_task_prompts_for_project_selection(tmp_path) -> None:
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=SessionStore(tmp_path / "sessions.sqlite3"))

    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/write Draft a Telegram rollout",
        )
    )

    assert "No project is currently bound" in response.text
    assert "/use <workspace>" in response.text


def test_worker_and_tasks_report_project_runtime(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.sqlite3")
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=store)
    _bind_workspace(adapter)
    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/write Build a multi-agent rollout workflow",
        )
    )
    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/execute Plan the architecture, decide whether sub-agents are needed, then implement safely",
        )
    )

    worker = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/worker",
        )
    )
    tasks = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/tasks",
        )
    )

    assert "Sub-agent Runs:" in worker.text
    assert "Latest Handoffs:" in worker.text
    assert "Recent Worker TMUX:" in worker.text
    assert "Recent Tasks:" in tasks.text
    assert "Build a multi-agent rollout workflow" in tasks.text


def test_projects_lists_current_workspace(tmp_path) -> None:
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=SessionStore(tmp_path / "sessions.sqlite3"))

    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/projects",
        )
    )

    assert "Projects:" in response.text
    assert "temporary" in response.text
    assert "No project is currently bound to this chat." in response.text


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
    assert "Mode: formal" in response.text
    assert "Path:" in response.text
    assert "Profile: Sheep GWAS analysis workspace for plotting, QC, and result interpretation." in response.text


def test_use_creates_unknown_workspace(tmp_path) -> None:
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=SessionStore(tmp_path / "sessions.sqlite3"))

    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/use does-not-exist",
        )
    )

    assert "switched to `does-not-exist`" in response.text


def test_use_temporary_clears_binding_and_ephemeral_context(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.sqlite3")
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=store)

    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="Brainstorm a rollout approach for this idea",
        )
    )
    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/use temporary",
        )
    )

    assert "switched to `temporary`" in response.text
    assert store.get_chat_binding("telegram:chat-1:root") is None
    assert store.list_ephemeral_messages("telegram:chat-1:root", "__ephemeral__", "claude") == []


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
    assert "just send a normal message" in response.text
    assert "Current workspace: sheep-gwas" in where.text
    assert "Executor Session: exec-" in where.text
    assert "Claude Session: claude-" in where.text
    assert "Codex Session: codex-" in where.text
    assert "Phase: discussion" in where.text


def test_quit_clears_workspace_binding(tmp_path) -> None:
    store = SessionStore(tmp_path / "sessions.sqlite3")
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=store)
    _bind_workspace(adapter, workspace_id="sheep-gwas")

    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/quit",
        )
    )

    assert "Exited current project mode." in response.text
    assert store.get_chat_binding("telegram:chat-1:root") is None


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
    workspace_id = _bind_workspace(adapter)
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
    handoffs = store.list_task_handoffs("telegram:chat-1:root", workspace_id, write_task_id := response.task_id or "")

    assert len(executor.prompts) == 4
    assert "Assigned Agent: claude" in executor.prompts[0]
    assert any("Assigned Agent: codex" in prompt for prompt in executor.prompts)
    assert "Assigned Agent: claude" in executor.prompts[-1]
    assert "Phase: reported" in response.text
    assert "Planning:" in response.text
    assert "Subtasks:" in response.text
    assert "TMUX:" in response.text
    assert "Result:" in response.text
    assert "Coordination: planned execution" in response.text
    assert "Roles: trigger=claude | orchestrator=claude | planner=claude | executor=codex | reviewer=claude" in response.text
    assert "Return Target: claude" in response.text
    assert "- Execution Backend: echo" in response.text
    assert "- Report Backend: echo" in response.text
    assert "Phase: reported" in status.text
    handoff_types = [row["handoff_type"] for row in handoffs]
    assert handoff_types[0] == "discussion_brief"
    assert "execution_packet" in handoff_types
    assert "verification_packet" in handoff_types
    assert "verification_result" in handoff_types
    assert handoff_types[-1] == "review_verdict"


def test_large_task_execute_runs_planning_before_codex(tmp_path) -> None:
    class RecordingExecutor(EchoExecutor):
        def __init__(self):
            self.prompts = []

        def run(self, prompt: str):
            self.prompts.append(prompt)
            return super().run(prompt)

    store = SessionStore(tmp_path / "sessions.sqlite3")
    executor = RecordingExecutor()
    adapter = CCConnectAdapter(executor=executor, store=store)
    workspace_id = _bind_workspace(adapter)

    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/write Design a multi-agent swarm architecture for a large refactor with tests and docs",
        )
    )

    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/execute Plan the architecture, decide whether sub-agents are needed, then implement safely",
        )
    )

    handoffs = store.list_task_handoffs("telegram:chat-1:root", workspace_id, response.task_id or "")

    assert len(executor.prompts) == 8
    assert any("Worker Phase: planning" in prompt and "Assigned Agent: claude" in prompt for prompt in executor.prompts)
    assert any("Assigned Agent: codex" in prompt and "subagent_role" in prompt for prompt in executor.prompts)
    assert any("Assigned Agent: codex" in prompt and "subagent_results" in prompt for prompt in executor.prompts)
    assert "Assigned Agent: claude" in executor.prompts[-1]
    assert "Planning:" in response.text
    assert "Subtasks:" in response.text
    assert "TMUX:" in response.text
    assert "Result:" in response.text
    assert "Complexity: large" in response.text
    assert "Coordination: swarm collaboration (3 sub-agent runs)" in response.text
    assert "Roles: trigger=claude | orchestrator=claude | planner=claude | executor=codex | reviewer=claude" in response.text
    assert "Return Target: claude" in response.text
    assert "Orchestrator TMUX: claude" in response.text
    assert "[background]" in response.text
    assert "Worker TMUX [isolated-implementation]: codex" in response.text
    assert "- Backend: echo" in response.text
    assert "- Sub-agent Runs: 3" in response.text
    handoff_types = [row["handoff_type"] for row in handoffs]
    assert "execution_packet" in handoff_types
    assert handoff_types.count("subagent_packet") == 3
    assert handoff_types.count("subagent_result") == 3
    assert "verification_packet" in handoff_types
    assert "verification_result" in handoff_types
    assert handoff_types[-1] == "review_verdict"
    assert "suggested_subagents" in handoffs[1]["content_json"]
    assert any('"project_memory"' in row["content_json"] for row in handoffs if row["handoff_type"] == "subagent_packet")
    assert any('"recent_hints"' in row["content_json"] for row in handoffs if row["handoff_type"] == "subagent_packet")
    tasks = store.list_tasks("telegram:chat-1:root", workspace_id)
    assert tasks[0].status == "completed"


def test_codex_backed_workspace_uses_codex_as_driver_and_claude_as_planner(tmp_path) -> None:
    class RecordingExecutor(EchoExecutor):
        def __init__(self):
            self.prompts = []

        def run(self, prompt: str):
            self.prompts.append(prompt)
            return super().run(prompt)

    store = SessionStore(tmp_path / "sessions.sqlite3")
    executor = RecordingExecutor()
    adapter = CCConnectAdapter(executor=executor, store=store)
    workspace_id = _bind_workspace(adapter)
    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/project set-backend codex",
        )
    )

    write_response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/write Design a large implementation with tests and docs",
        )
    )
    execute_response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/execute Plan first, then execute with subagents if needed",
        )
    )

    assert "Assigned Agent: codex" in executor.prompts[0]
    assert any("Worker Phase: planning" in prompt and "Assigned Agent: claude" in prompt for prompt in executor.prompts)
    assert "Roles: trigger=codex | orchestrator=claude | planner=claude | executor=codex | reviewer=claude" in write_response.text
    assert "Return Target: claude" in execute_response.text
    assert "Orchestrator TMUX: claude" in execute_response.text
    assert "Coordination:" in execute_response.text
    assert write_response.task_id is not None


def test_large_task_execute_launches_codex_worker_panes(tmp_path, monkeypatch) -> None:
    class RecordingExecutor(EchoExecutor):
        def __init__(self):
            self.prompts = []

        def run(self, prompt: str):
            self.prompts.append(prompt)
            return super().run(prompt)

    launched = []
    cleaned = []

    def fake_ensure_orchestrator_pane(*, project_id: str, workspace_path: str, provider: str = "claude"):
        launched.append((project_id, workspace_path, provider))
        return {
            "status": "launched",
            "launch_kind": "session",
            "provider": provider,
            "pane_id": f"%{len(launched)}",
            "session_name": f"ash-{provider}-{len(launched)}",
            "window_index": "0",
        }

    def fake_cleanup_tmux_launch(launch):
        cleaned.append(dict(launch))
        return {"status": "cleaned", "target": launch.get("session_name", "")}

    monkeypatch.setattr("agent_swarm_hub.adapter.ensure_orchestrator_pane", fake_ensure_orchestrator_pane)
    monkeypatch.setattr("agent_swarm_hub.adapter.cleanup_tmux_launch", fake_cleanup_tmux_launch)
    store = SessionStore(tmp_path / "sessions.sqlite3")
    executor = RecordingExecutor()
    adapter = CCConnectAdapter(executor=executor, store=store)
    workspace_id = _bind_workspace(adapter)

    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/write Design a multi-agent swarm architecture for a large refactor with tests and docs",
        )
    )
    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/execute Plan the architecture, decide whether sub-agents are needed, then implement safely",
        )
    )

    handoffs = store.list_task_handoffs("telegram:chat-1:root", workspace_id, response.task_id or "")
    subagent_packets = [row for row in handoffs if row["handoff_type"] == "subagent_packet"]
    subagent_results = [row for row in handoffs if row["handoff_type"] == "subagent_result"]

    assert launched[0][2] == "claude"
    assert all(item[2] == "codex" for item in launched[1:])
    assert len(cleaned) == 3
    assert len(subagent_packets) == 3
    assert len(subagent_results) == 3
    assert all('"worker_launch"' in row["content_json"] for row in subagent_packets)
    assert all('"worker_launch"' in row["content_json"] for row in subagent_results)
    assert all('"worker_cleanup"' in row["content_json"] for row in subagent_results)
    assert all('"strategy"' in row["content_json"] for row in subagent_results)


def test_large_task_execute_records_cleanup_when_worker_times_out(tmp_path, monkeypatch) -> None:
    class RecordingExecutor(EchoExecutor):
        def __init__(self):
            self.prompts = []

        def run(self, prompt: str):
            self.prompts.append(prompt)
            return super().run(prompt)

    launched = []
    cleaned = []

    def fake_ensure_orchestrator_pane(*, project_id: str, workspace_path: str, provider: str = "claude"):
        launched.append((project_id, workspace_path, provider))
        return {
            "status": "launched",
            "launch_kind": "session",
            "provider": provider,
            "pane_id": f"%{len(launched)}",
            "session_name": f"ash-{provider}-{len(launched)}",
            "window_index": "0",
        }

    def fake_cleanup_tmux_launch(launch):
        cleaned.append(dict(launch))
        return {"status": "cleaned", "target": launch.get("session_name", "")}

    monkeypatch.setattr("agent_swarm_hub.adapter.ensure_orchestrator_pane", fake_ensure_orchestrator_pane)
    monkeypatch.setattr("agent_swarm_hub.adapter.cleanup_tmux_launch", fake_cleanup_tmux_launch)

    store = SessionStore(tmp_path / "sessions.sqlite3")
    executor = RecordingExecutor()
    adapter = CCConnectAdapter(executor=executor, store=store)
    workspace_id = _bind_workspace(adapter)

    original_run = adapter.worker_pool.run

    def fake_run(**kwargs):
        if kwargs.get("mode") == "codex" and kwargs.get("executor_session_id", "").startswith("isolated-implementation-"):
            raise TimeoutError("simulated timeout")
        return original_run(**kwargs)

    monkeypatch.setattr(adapter.worker_pool, "run", fake_run)

    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/write Design a multi-agent swarm architecture for a large refactor with tests and docs",
        )
    )
    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/execute Plan the architecture, decide whether sub-agents are needed, then implement safely",
        )
    )

    handoffs = store.list_task_handoffs("telegram:chat-1:root", workspace_id, response.task_id or "")
    subagent_results = [row for row in handoffs if row["handoff_type"] == "subagent_result"]

    assert cleaned
    assert subagent_results
    assert all('"worker_cleanup"' in row["content_json"] for row in subagent_results)
    assert any("simulated timeout" in row["content_json"] for row in subagent_results)
    assert "Planning:" in response.text
    assert "- Planned Sub-agents:" in response.text
    assert "Subtasks:" in response.text
    assert "- Sub-agent Runs:" in response.text
    assert "Worker TMUX [isolated-implementation]" in response.text
    assert "Worker Cleanup [isolated-implementation]" in response.text


def test_build_executor_skips_codex_session_reuse_when_pane_is_dead(tmp_path, monkeypatch) -> None:
    work_dir = tmp_path / "project"
    work_dir.mkdir()

    monkeypatch.setenv("ASH_ASK_BIN", "/Users/sunxiangrong/.local/bin/ask")
    monkeypatch.setattr("agent_swarm_hub.executor._askd_available", lambda ask_command: True)
    monkeypatch.setattr("agent_swarm_hub.executor._should_skip_codex_session_reuse", lambda wd: True)

    executor = build_executor_for_config(
        mode="codex",
        transport="auto",
        work_dir=str(work_dir),
    )

    assert isinstance(executor, SkipPrimaryExecutor)


def test_should_skip_codex_session_reuse_caches_recent_dead_pane_result(tmp_path, monkeypatch) -> None:
    work_dir = tmp_path / "project"
    work_dir.mkdir()

    class FakeSession:
        def __init__(self):
            self.calls = 0

        def ensure_pane(self):
            self.calls += 1
            return False, "Session pane not available: Pane not alive and respawn failed"

    fake_session = FakeSession()

    monkeypatch.setattr("agent_swarm_hub.executor.ccb_lib_dir", lambda: work_dir)
    monkeypatch.setattr("agent_swarm_hub.executor.Path.cwd", lambda: work_dir)

    import agent_swarm_hub.executor as executor_mod

    executor_mod._CODEX_SESSION_REUSE_SKIP_CACHE.clear()

    import types

    monkeypatch.setitem(__import__("sys").modules, "caskd_session", types.SimpleNamespace(load_project_session=lambda _: fake_session))

    first = executor_mod._should_skip_codex_session_reuse(str(work_dir))
    second = executor_mod._should_skip_codex_session_reuse(str(work_dir))

    assert first is True
    assert second is True
    assert fake_session.calls == 1


def test_codex_prompt_loads_execution_guidance_docs(tmp_path) -> None:
    class RecordingExecutor(EchoExecutor):
        def __init__(self):
            self.prompts = []

        def run(self, prompt: str):
            self.prompts.append(prompt)
            return super().run(prompt)

    adapter = CCConnectAdapter(executor=RecordingExecutor(), store=SessionStore(tmp_path / "sessions.sqlite3"))
    _bind_workspace(adapter)
    response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/write Implement the rollout plan",
        )
    )

    assert response.task_id is not None
    prompt = adapter.executor.prompts[0]
    assert "The remote chat shell and local swarm shell should share the same command logic." in prompt


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
    workspace_id = _bind_workspace(adapter)

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

    claude_rows = store.list_recent_agent_messages("telegram:chat-1:root", workspace_id, "claude")
    codex_rows = store.list_recent_agent_messages("telegram:chat-1:root", workspace_id, "codex")

    assert len(claude_rows) >= 4
    assert codex_rows == []


def test_unbound_ephemeral_history_is_not_injected_into_formal_project_prompt(tmp_path) -> None:
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
    _bind_workspace(adapter)
    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/write Draft the rollout plan",
        )
    )

    assert "ephemeral user: hello" not in executor.prompts[0]
    ephemerals = store.list_ephemeral_messages("telegram:chat-1:root", "__ephemeral__", "claude")
    assert ephemerals == []


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
    assert "Focus: GWAS plotting" in executor.prompts[0]
    assert "Recent Memory Hints:" in executor.prompts[0]
    assert "user: Need GWAS plotting help" in executor.prompts[0]
    assert "assistant: Prior GWAS plotting suggestions" in executor.prompts[0]
    assert "Summary: Project: sheep-gwas" not in executor.prompts[0]
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
    default_workspace = "project-alpha"
    _bind_workspace(adapter, workspace_id=default_workspace)
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
    workspace_id = _bind_workspace(adapter)
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

    record = store.get_workspace_session("telegram:chat-1:root", workspace_id)

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
    workspace_id = _bind_workspace(adapter)

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

    record = store.get_workspace_session("telegram:chat-1:root", workspace_id)

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
    _bind_workspace(adapter)
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
    _bind_workspace(adapter)
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


def test_blocker_event_becomes_visible_escalation(tmp_path) -> None:
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=SessionStore(tmp_path / "sessions.sqlite3"))
    adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.LARK,
            chat_id="chat-2",
            user_id="user-9",
            text="/use lark-project",
        )
    )
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
