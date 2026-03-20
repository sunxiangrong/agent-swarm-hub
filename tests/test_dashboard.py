import sqlite3
import json

from agent_swarm_hub.dashboard import build_dashboard_snapshot
import agent_swarm_hub.dashboard.snapshot as dashboard_snapshot
import agent_swarm_hub.dashboard.server as dashboard_server
from agent_swarm_hub.project_context import ProjectContextStore
from agent_swarm_hub.session_store import SessionStore


def test_build_dashboard_snapshot_includes_project_memory_and_runtime(tmp_path) -> None:
    project_db = tmp_path / "projects.sqlite3"
    session_db = tmp_path / "runtime.sqlite3"
    workspace_path = tmp_path / "agent-browser"
    workspace_path.mkdir()

    with sqlite3.connect(project_db) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (
                project_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                workspace_path TEXT NOT NULL DEFAULT '',
                profile TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
            CREATE TABLE project_sessions (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                title TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, session_id)
            );
            """
        )
        conn.execute(
            "INSERT INTO projects (project_id, title, workspace_path, profile, summary) VALUES (?, ?, ?, ?, ?)",
            ("agent-browser", "agent-browser", str(workspace_path), "Browser automation", "stale"),
        )
        conn.execute(
            "INSERT INTO project_memory (project_id, focus, recent_context, memory, recent_hints_json) VALUES (?, ?, ?, ?, ?)",
            (
                "agent-browser",
                "chrome会做的更好吗",
                "已经确认当前问题是项目级上下文摘要过度退化",
                "Compare whether Chrome-native tooling would produce a more reliable browser workflow.",
                '["user: 整理项目级长期记忆"]',
            ),
        )
        conn.execute(
            "INSERT INTO provider_bindings (project_id, provider, raw_session_id) VALUES (?, ?, ?)",
            ("agent-browser", "codex", "codex-current"),
        )
        conn.execute(
            "INSERT INTO project_sessions (project_id, provider, session_id, status, title) VALUES (?, ?, ?, ?, ?)",
            ("agent-browser", "codex", "codex-current", "active", "Current task"),
        )

    session_store = SessionStore(session_db)
    session_store.upsert_workspace(
        workspace_id="agent-browser",
        title="agent-browser",
        path=str(workspace_path),
        backend="codex",
        transport="direct",
    )
    session_store.upsert_workspace_session(
        session_key="local-cli",
        workspace_id="agent-browser",
        active_task_id="task-1",
        executor_session_id="exec-1",
        claude_session_id="claude-1",
        codex_session_id="codex-1",
        phase="discussion",
        conversation_summary="Task: chrome会做的更好吗\nRecent: 先做一个只读 dashboard",
        swarm_state_json="",
        escalations_json="[]",
    )

    payload = build_dashboard_snapshot(
        project_store=ProjectContextStore(str(project_db)),
        session_store=session_store,
    )

    assert payload["project_count"] == 1
    assert payload["active_project_count"] == 1
    project = payload["projects"][0]
    assert project["project_id"] == "agent-browser"
    assert project["focus"] == "chrome会做的更好吗"
    assert project["next_step"] == "整理项目级长期记忆"
    assert project["current_sessions"] == "codex: codex-current"
    assert project["status"] == "discussion"
    assert project["live_phase"] == "discussion"
    assert "只读 dashboard" in project["live_summary"]
    assert project["current_trigger"] == "codex"
    assert project["current_driver"] == "claude"
    assert project["review_return_target"] == "claude"
    assert project["driver_session_id"] == "claude-1"
    assert payload["active_projects"][0]["project_id"] == "agent-browser"


def test_build_dashboard_snapshot_includes_workspace_only_project(tmp_path) -> None:
    project_db = tmp_path / "projects.sqlite3"
    session_db = tmp_path / "runtime.sqlite3"
    workspace_path = tmp_path / "agent-browser"
    workspace_path.mkdir()

    with sqlite3.connect(project_db) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (
                project_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                workspace_path TEXT NOT NULL DEFAULT '',
                profile TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE project_memory (
                project_id TEXT PRIMARY KEY,
                focus TEXT NOT NULL DEFAULT '',
                recent_context TEXT NOT NULL DEFAULT '',
                memory TEXT NOT NULL DEFAULT '',
                recent_hints_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

    session_store = SessionStore(session_db)
    session_store.upsert_workspace(
        workspace_id="agent-browser",
        title="agent-browser",
        path=str(workspace_path),
        backend="claude",
        transport="direct",
    )

    payload = build_dashboard_snapshot(
        project_store=ProjectContextStore(str(project_db)),
        session_store=session_store,
    )

    assert payload["project_count"] == 1
    assert payload["projects"][0]["project_id"] == "agent-browser"
    assert payload["projects"][0]["workspace_path"] == str(workspace_path)


def test_build_dashboard_snapshot_prefers_pinned_projects(tmp_path) -> None:
    project_db = tmp_path / "projects.sqlite3"
    session_db = tmp_path / "runtime.sqlite3"
    alpha_path = tmp_path / "alpha"
    beta_path = tmp_path / "beta"
    alpha_path.mkdir()
    beta_path.mkdir()

    with sqlite3.connect(project_db) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (
                project_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                workspace_path TEXT NOT NULL DEFAULT '',
                profile TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE project_memory (
                project_id TEXT PRIMARY KEY,
                focus TEXT NOT NULL DEFAULT '',
                recent_context TEXT NOT NULL DEFAULT '',
                memory TEXT NOT NULL DEFAULT '',
                recent_hints_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.executemany(
            "INSERT INTO projects (project_id, title, workspace_path, profile, summary) VALUES (?, ?, ?, '', '')",
            [
                ("alpha", "alpha", str(alpha_path)),
                ("beta", "beta", str(beta_path)),
            ],
        )

    project_store = ProjectContextStore(str(project_db))
    project_store.set_project_pinned("beta", True)
    payload = build_dashboard_snapshot(
        project_store=project_store,
        session_store=SessionStore(session_db),
    )

    assert payload["pinned_project_count"] == 1
    assert payload["watched_projects"][0]["project_id"] == "beta"
    assert payload["projects"][0]["project_id"] == "beta"
    assert payload["projects"][0]["pinned"] is True


def test_build_dashboard_snapshot_includes_tmux_preview(monkeypatch, tmp_path) -> None:
    project_db = tmp_path / "projects.sqlite3"
    session_db = tmp_path / "runtime.sqlite3"
    workspace_path = tmp_path / "agent-browser"
    workspace_path.mkdir()

    with sqlite3.connect(project_db) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (
                project_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                workspace_path TEXT NOT NULL DEFAULT '',
                profile TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE project_memory (
                project_id TEXT PRIMARY KEY,
                focus TEXT NOT NULL DEFAULT '',
                recent_context TEXT NOT NULL DEFAULT '',
                memory TEXT NOT NULL DEFAULT '',
                recent_hints_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            "INSERT INTO projects (project_id, title, workspace_path, profile, summary) VALUES (?, ?, ?, '', '')",
            ("agent-browser", "agent-browser", str(workspace_path)),
        )

    monkeypatch.setattr(
        dashboard_snapshot,
        "load_tmux_project_panes",
        lambda: {
            str(workspace_path.resolve()): [
                {
                    "session_name": "ash",
                    "window_name": "main",
                    "pane_id": "%1",
                    "pane_title": "agent-browser",
                    "active": True,
                    "current_path": str(workspace_path.resolve()),
                    "preview": "pytest running | dashboard preview | done",
                }
            ]
        },
    )

    payload = build_dashboard_snapshot(
        project_store=ProjectContextStore(str(project_db)),
        session_store=SessionStore(session_db),
    )

    project = payload["projects"][0]
    assert project["tmux_preview"] == "pytest running | dashboard preview | done"
    assert project["active"] is True


def test_build_dashboard_snapshot_includes_swarm_activity(tmp_path) -> None:
    project_db = tmp_path / "projects.sqlite3"
    session_db = tmp_path / "runtime.sqlite3"
    workspace_path = tmp_path / "agent-browser"
    workspace_path.mkdir()

    with sqlite3.connect(project_db) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (
                project_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                workspace_path TEXT NOT NULL DEFAULT '',
                profile TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE project_memory (
                project_id TEXT PRIMARY KEY,
                focus TEXT NOT NULL DEFAULT '',
                recent_context TEXT NOT NULL DEFAULT '',
                memory TEXT NOT NULL DEFAULT '',
                recent_hints_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            "INSERT INTO projects (project_id, title, workspace_path, profile, summary) VALUES (?, ?, ?, '', '')",
            ("agent-browser", "agent-browser", str(workspace_path)),
        )

    session_store = SessionStore(session_db)
    session_store.upsert_workspace(
        workspace_id="agent-browser",
        title="agent-browser",
        path=str(workspace_path),
        backend="claude",
        transport="ccb",
    )
    session_store.upsert_workspace_session(
        session_key="local-cli",
        workspace_id="agent-browser",
        active_task_id="task-1",
        executor_session_id="exec-1",
        claude_session_id="claude-1",
        codex_session_id="codex-1",
        phase="executing",
        conversation_summary="Task: improve swarm visibility\nRecent: coordinating sub-agents",
        swarm_state_json='{"root_task_id":"task-1"}',
        escalations_json="[]",
    )
    session_store.append_task_handoff(
        session_key="local-cli",
        workspace_id="agent-browser",
        task_id="task-1",
        handoff_type="subagent_packet",
        source_agent="worker",
        target_agent="researcher",
        content_json=json.dumps({"task": "Inspect current dashboard swarm visibility."}, ensure_ascii=False),
    )
    session_store.append_task_handoff(
        session_key="local-cli",
        workspace_id="agent-browser",
        task_id="task-1",
        handoff_type="subagent_result",
        source_agent="researcher",
        target_agent="worker",
        content_json=json.dumps({"backend": "codex", "output": "Found missing swarm agent visibility in dashboard."}, ensure_ascii=False),
    )

    payload = build_dashboard_snapshot(
        project_store=ProjectContextStore(str(project_db)),
        session_store=session_store,
    )

    project = payload["projects"][0]
    assert project["swarm_active"] is True
    assert project["swarm_session_key"] == "local-cli"
    assert project["swarm_task_id"] == "task-1"
    assert project["swarm_agent_count"] == 1
    assert project["swarm_handoff_count"] == 2
    assert project["swarm_roles"]["trigger"] == "claude"
    assert project["swarm_roles"]["orchestrator"] == "claude"
    assert project["swarm_roles"]["executor"] == "codex"
    assert project["driver_session_id"] == "claude-1"
    assert project["swarm_agents"][0]["name"] == "researcher"
    assert project["swarm_agents"][0]["status"] == "completed"


def test_build_dashboard_snapshot_maps_driver_to_tmux_pane(monkeypatch, tmp_path) -> None:
    project_db = tmp_path / "projects.sqlite3"
    session_db = tmp_path / "runtime.sqlite3"
    workspace_path = tmp_path / "agent-browser"
    workspace_path.mkdir()

    with sqlite3.connect(project_db) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (
                project_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                workspace_path TEXT NOT NULL DEFAULT '',
                profile TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE project_memory (
                project_id TEXT PRIMARY KEY,
                focus TEXT NOT NULL DEFAULT '',
                recent_context TEXT NOT NULL DEFAULT '',
                memory TEXT NOT NULL DEFAULT '',
                recent_hints_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            "INSERT INTO projects (project_id, title, workspace_path, profile, summary) VALUES (?, ?, ?, '', '')",
            ("agent-browser", "agent-browser", str(workspace_path)),
        )

    session_store = SessionStore(session_db)
    session_store.upsert_workspace(
        workspace_id="agent-browser",
        title="agent-browser",
        path=str(workspace_path),
        backend="codex",
        transport="ccb",
    )
    session_store.upsert_workspace_session(
        session_key="local-cli",
        workspace_id="agent-browser",
        active_task_id="task-1",
        executor_session_id="exec-1",
        claude_session_id="claude-1",
        codex_session_id="codex-1",
        phase="executing",
        conversation_summary="Task: driver pane mapping",
        swarm_state_json='{"root_task_id":"task-1"}',
        escalations_json="[]",
    )
    monkeypatch.setattr(
        dashboard_snapshot,
        "load_tmux_project_panes",
        lambda: {
            str(workspace_path.resolve()): [
                {
                    "session_name": "ash",
                    "window_index": "0",
                    "window_name": "main",
                    "pane_id": "%1",
                    "pane_title": "ash-chat | agent-browser | claude",
                    "active": False,
                    "current_path": str(workspace_path.resolve()),
                    "preview": "claude pane",
                },
                {
                    "session_name": "ash",
                    "window_index": "0",
                    "window_name": "main",
                    "pane_id": "%2",
                    "pane_title": "ash-chat | agent-browser | codex",
                    "active": True,
                    "current_path": str(workspace_path.resolve()),
                    "preview": "codex pane",
                },
            ]
        },
    )

    payload = build_dashboard_snapshot(
        project_store=ProjectContextStore(str(project_db)),
        session_store=session_store,
    )

    project = payload["projects"][0]
    assert project["current_trigger"] == "codex"
    assert project["current_driver"] == "claude"
    assert project["driver_tmux_pane_id"] == "%1"
    assert project["driver_tmux_session_name"] == "ash"
    assert project["driver_tmux_window_index"] == "0"
    assert "claude" in project["driver_tmux_title"].lower()


def test_build_dashboard_snapshot_includes_live_ccb_providers(monkeypatch, tmp_path) -> None:
    project_db = tmp_path / "projects.sqlite3"
    session_db = tmp_path / "runtime.sqlite3"
    workspace_path = tmp_path / "knowledge-system"
    workspace_path.mkdir()
    registry_dir = tmp_path / "home" / ".ccb" / "run"
    registry_dir.mkdir(parents=True)

    with sqlite3.connect(project_db) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (
                project_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                workspace_path TEXT NOT NULL DEFAULT '',
                profile TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE project_memory (
                project_id TEXT PRIMARY KEY,
                focus TEXT NOT NULL DEFAULT '',
                recent_context TEXT NOT NULL DEFAULT '',
                memory TEXT NOT NULL DEFAULT '',
                recent_hints_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            "INSERT INTO projects (project_id, title, workspace_path, profile, summary) VALUES (?, ?, ?, '', '')",
            ("knowledge-system", "knowledge-system", str(workspace_path)),
        )

    (registry_dir / "ccb-session-demo.json").write_text(
        json.dumps(
            {
                "ccb_session_id": "demo",
                "work_dir": str(workspace_path),
                "updated_at": 2_000_000_000,
                "providers": {
                    "claude": {
                        "pane_id": "%11",
                        "pane_title_marker": "ash-chat | knowledge-system | claude",
                        "claude_session_id": "claude-session-1",
                    },
                    "codex": {
                        "pane_id": "%12",
                        "pane_title_marker": "ash-chat | knowledge-system | codex",
                        "codex_session_id": "codex-session-1",
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    payload = build_dashboard_snapshot(
        project_store=ProjectContextStore(str(project_db)),
        session_store=SessionStore(session_db),
    )

    project = payload["projects"][0]
    assert project["project_id"] == "knowledge-system"
    assert project["ccb_live_count"] == 2
    assert {item["provider"] for item in project["ccb_live_providers"]} == {"claude", "codex"}
    assert project["active"] is True


def test_sync_project_memory_runs_all_artifact_updates(tmp_path) -> None:
    project_db = tmp_path / "projects.sqlite3"
    workspace_path = tmp_path / "agent-browser"
    workspace_path.mkdir()

    with sqlite3.connect(project_db) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (
                project_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                workspace_path TEXT NOT NULL DEFAULT '',
                profile TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            "INSERT INTO projects (project_id, title, workspace_path, profile, summary) VALUES (?, ?, ?, '', '')",
            ("agent-browser", "agent-browser", str(workspace_path)),
        )

    store = ProjectContextStore(str(project_db))
    assert dashboard_server._sync_project_memory("agent-browser", store) is True
    assert (workspace_path / "PROJECT_MEMORY.md").exists()
    assert (workspace_path / "PROJECT_SKILL.md").exists()


def test_open_project_path_launches_open_command(monkeypatch, tmp_path) -> None:
    project_db = tmp_path / "projects.sqlite3"
    workspace_path = tmp_path / "agent-browser"
    workspace_path.mkdir()

    with sqlite3.connect(project_db) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (
                project_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                workspace_path TEXT NOT NULL DEFAULT '',
                profile TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            "INSERT INTO projects (project_id, title, workspace_path, profile, summary) VALUES (?, ?, ?, '', '')",
            ("agent-browser", "agent-browser", str(workspace_path)),
        )

    called = {}

    class FakeProcess:
        pass

    def fake_popen(argv):
        called["argv"] = argv
        return FakeProcess()

    monkeypatch.setattr(dashboard_server.subprocess, "Popen", fake_popen)

    assert dashboard_server._open_project_path("agent-browser", ProjectContextStore(str(project_db))) is True
    assert called["argv"] == ["open", str(workspace_path)]


def test_focus_driver_pane_selects_tmux_pane(monkeypatch, tmp_path) -> None:
    project_db = tmp_path / "projects.sqlite3"
    session_db = tmp_path / "runtime.sqlite3"
    workspace_path = tmp_path / "agent-browser"
    workspace_path.mkdir()

    with sqlite3.connect(project_db) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (
                project_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                workspace_path TEXT NOT NULL DEFAULT '',
                profile TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE project_memory (
                project_id TEXT PRIMARY KEY,
                focus TEXT NOT NULL DEFAULT '',
                recent_context TEXT NOT NULL DEFAULT '',
                memory TEXT NOT NULL DEFAULT '',
                recent_hints_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            "INSERT INTO projects (project_id, title, workspace_path, profile, summary) VALUES (?, ?, ?, '', '')",
            ("agent-browser", "agent-browser", str(workspace_path)),
        )

    session_store = SessionStore(session_db)
    session_store.upsert_workspace(
        workspace_id="agent-browser",
        title="agent-browser",
        path=str(workspace_path),
        backend="codex",
        transport="ccb",
    )
    session_store.upsert_workspace_session(
        session_key="local-cli",
        workspace_id="agent-browser",
        active_task_id="task-1",
        executor_session_id="exec-1",
        claude_session_id="claude-1",
        codex_session_id="codex-1",
        phase="executing",
        conversation_summary="Task: focus driver pane",
        swarm_state_json='{"root_task_id":"task-1"}',
        escalations_json="[]",
    )
    monkeypatch.setenv("ASH_SESSION_DB", str(session_db))
    monkeypatch.setattr(
        dashboard_snapshot,
        "load_tmux_project_panes",
        lambda: {
            str(workspace_path.resolve()): [
                {
                    "session_name": "ash",
                    "window_index": "1",
                    "window_name": "main",
                    "pane_id": "%2",
                    "pane_title": "ash-chat | agent-browser | codex",
                    "active": True,
                    "current_path": str(workspace_path.resolve()),
                    "preview": "codex pane",
                }
            ]
        },
    )

    called = {}

    def fake_run(argv, check, capture_output, text):
        called.setdefault("argvs", []).append(argv)
        class Result:
            returncode = 0
        return Result()

    monkeypatch.setattr(dashboard_server.subprocess, "run", fake_run)

    assert dashboard_server._focus_driver_pane("agent-browser", ProjectContextStore(str(project_db))) is True
    assert called["argvs"] == [
        ["tmux", "switch-client", "-t", "ash"],
        ["tmux", "select-window", "-t", "ash:1"],
        ["tmux", "select-pane", "-t", "%2"],
    ]
