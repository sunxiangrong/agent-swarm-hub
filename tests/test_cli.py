import sqlite3
from pathlib import Path

from agent_swarm_hub.cli import main


def _write_codex_session(home: Path, session_id: str, cwd: str) -> Path:
    session_dir = home / ".codex" / "sessions" / "2026" / "03" / "17"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_file = session_dir / f"rollout-2026-03-17T10-00-00-{session_id}.jsonl"
    session_file.write_text(
        f'{{"timestamp":"2026-03-17T10:00:00Z","type":"session_meta","payload":{{"id":"{session_id}","cwd":"{cwd}"}}}}\n',
        encoding="utf-8",
    )
    return session_file


def test_cli_prints_lark_ws_config(monkeypatch, capsys) -> None:
    monkeypatch.setenv("ASH_LARK_ENABLED", "true")
    monkeypatch.setenv("ASH_LARK_APP_ID", "cli_app")
    monkeypatch.setenv("ASH_LARK_VERIFY_TOKEN", "verify")
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "lark-ws", "--print-config"])

    exit_code = main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "cli_app" in output
    assert "verify" in output


def test_cli_prints_telegram_poll_config(monkeypatch, capsys) -> None:
    monkeypatch.setenv("ASH_TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("ASH_TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "telegram-poll", "--print-config"])

    exit_code = main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "bot_token_configured" in output
    assert "True" in output


def test_cli_runs_telegram_poll_forever_by_default(monkeypatch) -> None:
    called = {}

    class FakePollingRunner:
        def __init__(self, service):
            called["service"] = service

        def run_forever(self, *, offset=None):
            called["offset"] = offset

    monkeypatch.setenv("ASH_TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("ASH_TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr("agent_swarm_hub.cli.TelegramPollingRunner", FakePollingRunner)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "telegram-poll"])

    exit_code = main()

    assert exit_code == 0
    assert called["offset"] is None


def test_cli_runs_lark_ws_forever(monkeypatch) -> None:
    called = {"started": False}

    class FakeRunner:
        @classmethod
        def create(cls, config):
            return cls()

        def run_forever(self):
            called["started"] = True

    monkeypatch.setenv("ASH_LARK_ENABLED", "true")
    monkeypatch.setenv("ASH_LARK_APP_ID", "cli_app")
    monkeypatch.setenv("ASH_LARK_APP_SECRET", "secret")
    monkeypatch.setenv("ASH_LARK_VERIFY_TOKEN", "verify")
    monkeypatch.setattr("agent_swarm_hub.cli.LarkWebSocketRunner", FakeRunner)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "lark-ws"])

    exit_code = main()

    assert exit_code == 0
    assert called["started"] is True


def test_cli_local_chat_binds_explicit_project(monkeypatch, capsys) -> None:
    inputs = iter(["/quit"])

    monkeypatch.setenv("ASH_EXECUTOR", "echo")
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    monkeypatch.setattr(
        "sys.argv",
        ["agent-swarm-hub", "local-chat", "--provider", "echo", "--project", "agent-swarm-hub"],
    )

    exit_code = main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Current workspace switched to `agent-swarm-hub`." in output


def test_cli_local_chat_prompts_for_project_or_temporary(monkeypatch, capsys) -> None:
    inputs = iter(["temporary", "/quit"])

    monkeypatch.setenv("ASH_EXECUTOR", "echo")
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-chat", "--provider", "echo"])

    exit_code = main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Available workspaces:" in output
    assert "Temporary mode selected." in output


def test_cli_local_native_can_add_project_from_picker(monkeypatch, tmp_path, capsys) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    invoke_dir = tmp_path / "new-project-dir"
    invoke_dir.mkdir()
    captured = {}

    def fake_execvpe(command, argv, env):
        captured["command"] = command
        captured["argv"] = argv
        captured["env"] = env
        raise SystemExit(0)

    inputs = iter(["My New Project"])
    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setenv("ASH_INVOKE_DIR", str(invoke_dir))
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("os.execvpe", fake_execvpe)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex"])

    try:
        main()
    except SystemExit as exc:
        assert exc.code == 0

    output = capsys.readouterr().out
    assert "No workspaces with an enterable path were found." in output
    assert "Added project `my-new-project`" in output
    assert captured["env"]["ASH_ACTIVE_WORKSPACE"] == "my-new-project"
    assert captured["env"]["ASH_PROJECT_PATH"] == str(invoke_dir)


def test_cli_local_chat_reprompts_for_invalid_project_selection(monkeypatch, capsys) -> None:
    inputs = iter(["project", "1", "/quit"])

    monkeypatch.setenv("ASH_EXECUTOR", "echo")
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-chat", "--provider", "echo"])

    exit_code = main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Unknown project selection." in output
    assert "Current workspace switched to `agent-swarm-hub`." in output


def test_cli_local_native_launches_provider_in_workspace(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    workspace_path = tmp_path / "project"
    workspace_path.mkdir()

    from agent_swarm_hub.session_store import SessionStore

    store = SessionStore(db_path)
    store.upsert_workspace(
        workspace_id="project-alpha",
        title="project-alpha",
        path=str(workspace_path),
        backend="codex",
        transport="direct",
    )

    captured = {}

    def fake_execvpe(command, argv, env):
        captured["command"] = command
        captured["argv"] = argv
        captured["env"] = env
        raise SystemExit(0)

    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setattr("os.execvpe", fake_execvpe)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex", "--project", "project-alpha"])

    try:
        main()
    except SystemExit as exc:
        assert exc.code == 0

    assert captured["command"].endswith("codex")
    assert captured["env"]["ASH_ACTIVE_WORKSPACE"] == "project-alpha"
    assert captured["env"]["CCB_WORK_DIR"] == str(workspace_path)
    assert captured["env"]["CCB_RUN_DIR"] == str(workspace_path)


def test_cli_local_native_resumes_shared_project_session(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    workspace_path = tmp_path / "project"
    workspace_path.mkdir()
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    fake_home = tmp_path / "home"

    from agent_swarm_hub.session_store import SessionStore

    store = SessionStore(db_path)
    store.upsert_workspace(
        workspace_id="project-alpha",
        title="project-alpha",
        path=str(workspace_path),
        backend="codex",
        transport="direct",
    )

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "project-alpha",
                "project-alpha",
                str(workspace_path),
                "Project alpha profile",
                "Project: project-alpha\nCurrent focus: Keep resume stable\nRecent context: Native CLI entry should reuse the right conversation",
            ),
        )
        conn.execute(
            """
            INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, notes, source_path, cwd, last_used_at)
            VALUES (?, ?, ?, 'active', '', '', '', '2026-03-17T10:00:00+00:00')
            """,
            ("codex", "codex-session-123", "project-alpha"),
        )

    captured = {}

    def fake_execvpe(command, argv, env):
        captured["command"] = command
        captured["argv"] = argv
        captured["env"] = env
        raise SystemExit(0)

    _write_codex_session(fake_home, "codex-session-123", str(workspace_path))
    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr("os.execvpe", fake_execvpe)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex", "--project", "project-alpha"])

    try:
        main()
    except SystemExit as exc:
        assert exc.code == 0

    assert captured["argv"][1:] == ["resume", "codex-session-123"]
    assert captured["env"]["ASH_PROVIDER_SESSION_ID"] == "codex-session-123"
    assert captured["env"]["ASH_CODEX_SESSION_ID"] == "codex-session-123"
    assert captured["env"]["ASH_PROJECT_PATH"] == str(workspace_path)
    assert captured["env"]["ASH_PROJECT_MEMORY_PROJECT_ID"] == "project-alpha"
    assert captured["env"]["ASH_PROJECT_MEMORY_PROFILE"] == "Project alpha profile"
    assert captured["env"]["ASH_PROJECT_MEMORY_FOCUS"] == "Keep resume stable"


def test_cli_local_native_skips_codex_resume_when_session_file_is_missing(monkeypatch, tmp_path, capsys) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    workspace_path = tmp_path / "project"
    workspace_path.mkdir()
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    fake_home = tmp_path / "home"
    (fake_home / ".codex" / "sessions").mkdir(parents=True)

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "project-alpha",
                "project-alpha",
                str(workspace_path),
                "Project alpha profile",
                "Project: project-alpha\nCurrent focus: Skip stale resume ids",
            ),
        )
        conn.execute(
            """
            INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, notes, source_path, cwd, last_used_at)
            VALUES (?, ?, ?, 'active', '', '', '', '2026-03-17T10:00:00+00:00')
            """,
            ("codex", "codex-session-missing", "project-alpha"),
        )

    captured = {}

    def fake_execvpe(command, argv, env):
        captured["command"] = command
        captured["argv"] = argv
        captured["env"] = env
        raise SystemExit(0)

    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr("os.execvpe", fake_execvpe)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex", "--project", "project-alpha"])

    try:
        main()
    except SystemExit as exc:
        assert exc.code == 0

    output = capsys.readouterr().out
    assert captured["argv"] == [captured["command"]]
    assert "memory_summary=" in output
    assert "Project selected: project-alpha" not in output
    assert "ASH_PROVIDER_SESSION_ID" not in captured["env"]


def test_cli_local_native_skips_codex_resume_when_session_workspace_mismatches(monkeypatch, tmp_path, capsys) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    workspace_path = tmp_path / "project"
    workspace_path.mkdir()
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    fake_home = tmp_path / "home"
    session_dir = fake_home / ".codex" / "sessions" / "2026" / "03" / "17"
    session_dir.mkdir(parents=True)
    session_file = session_dir / "rollout-2026-03-17T10-00-00-codex-session-123.jsonl"
    session_file.write_text(
        '{"timestamp":"2026-03-17T10:00:00Z","type":"session_meta","payload":{"id":"codex-session-123","cwd":"/tmp/wrong-workspace"}}\n',
        encoding="utf-8",
    )

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "project-alpha",
                "project-alpha",
                str(workspace_path),
                "Project alpha profile",
                "Project: project-alpha\nCurrent focus: Avoid wrong resumes",
            ),
        )
        conn.execute(
            """
            INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, notes, source_path, cwd, last_used_at)
            VALUES (?, ?, ?, 'active', '', '', '', '2026-03-17T10:00:00+00:00')
            """,
            ("codex", "codex-session-123", "project-alpha"),
        )

    captured = {}

    def fake_execvpe(command, argv, env):
        captured["command"] = command
        captured["argv"] = argv
        captured["env"] = env
        raise SystemExit(0)

    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr("os.execvpe", fake_execvpe)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex", "--project", "project-alpha"])

    try:
        main()
    except SystemExit as exc:
        assert exc.code == 0

    output = capsys.readouterr().out
    assert captured["argv"] == [captured["command"]]
    assert "memory_summary=" in output
    assert "Project selected: project-alpha" not in output
    assert "ASH_PROVIDER_SESSION_ID" not in captured["env"]


def test_cli_local_native_resolves_shared_workspace_without_local_row(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    workspace_path = tmp_path / "project"
    workspace_path.mkdir()
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    fake_home = tmp_path / "home"

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "project-alpha",
                "project-alpha",
                str(workspace_path),
                "Project alpha profile",
                "Project: project-alpha\nCurrent focus: Claude handoff\nRecent context: Re-enter the native Claude CLI in the correct path",
            ),
        )
        conn.execute(
            """
            INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, notes, source_path, cwd, last_used_at)
            VALUES (?, ?, ?, 'active', '', '', '', '2026-03-17T10:00:00+00:00')
            """,
            ("claude", "claude-session-abc", "project-alpha"),
        )

    captured = {}

    def fake_execvpe(command, argv, env):
        captured["command"] = command
        captured["argv"] = argv
        captured["env"] = env
        raise SystemExit(0)

    _write_codex_session(fake_home, "codex-session-123", str(workspace_path))
    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr("os.execvpe", fake_execvpe)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "claude", "--project", "project-alpha"])

    try:
        main()
    except SystemExit as exc:
        assert exc.code == 0

    assert captured["argv"][1:] == ["--resume", "claude-session-abc"]
    assert captured["env"]["ASH_ACTIVE_WORKSPACE"] == "project-alpha"
    assert captured["env"]["CCB_WORK_DIR"] == str(workspace_path)
    assert captured["env"]["ASH_CLAUDE_SESSION_ID"] == "claude-session-abc"
    assert captured["env"]["ASH_PROJECT_PATH"] == str(workspace_path)
    assert captured["env"]["ASH_PROJECT_MEMORY_PROJECT_ID"] == "project-alpha"
    assert captured["env"]["ASH_PROJECT_MEMORY_RECENT_CONTEXT"] == "Re-enter the native Claude CLI in the correct path"


def test_cli_local_native_prefers_provider_binding_over_latest_session(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    workspace_path = tmp_path / "project"
    workspace_path.mkdir()
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    fake_home = tmp_path / "home"

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
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
                "project-alpha",
                "project-alpha",
                str(workspace_path),
                "Project alpha profile",
                "Project: project-alpha\nCurrent focus: Prefer explicit bindings",
            ),
        )
        conn.execute(
            """
            INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, notes, source_path, cwd, last_used_at)
            VALUES (?, ?, ?, 'active', '', '', '', '2026-03-17T10:01:00+00:00')
            """,
            ("codex", "codex-session-latest", "project-alpha"),
        )
        conn.execute(
            """
            INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, notes, source_path, cwd, last_used_at)
            VALUES (?, ?, ?, 'archived', '', '', '', '2026-03-17T10:00:00+00:00')
            """,
            ("codex", "codex-session-bound", "project-alpha"),
        )
        conn.execute(
            """
            INSERT INTO provider_bindings (project_id, provider, raw_session_id)
            VALUES (?, ?, ?)
            """,
            ("project-alpha", "codex", "codex-session-bound"),
        )

    captured = {}

    def fake_execvpe(command, argv, env):
        captured["command"] = command
        captured["argv"] = argv
        captured["env"] = env
        raise SystemExit(0)

    _write_codex_session(fake_home, "codex-session-latest", str(workspace_path))
    _write_codex_session(fake_home, "codex-session-bound", str(workspace_path))
    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr("os.execvpe", fake_execvpe)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex", "--project", "project-alpha"])

    try:
        main()
    except SystemExit as exc:
        assert exc.code == 0

    assert captured["argv"][1:] == ["resume", "codex-session-bound"]
    assert captured["env"]["ASH_PROVIDER_SESSION_ID"] == "codex-session-bound"


def test_cli_local_native_exports_both_project_provider_sessions(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    workspace_path = tmp_path / "project"
    workspace_path.mkdir()
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    fake_home = tmp_path / "home"

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "project-alpha",
                "project-alpha",
                str(workspace_path),
                "Project alpha profile",
                "Project: project-alpha\nCurrent focus: Coordinate Claude and Codex\nRecent context: Keep both provider bindings visible",
            ),
        )
        conn.execute(
            """
            INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, notes, source_path, cwd, last_used_at)
            VALUES (?, ?, ?, 'active', '', '', '', '2026-03-17T10:00:00+00:00')
            """,
            ("claude", "claude-session-abc", "project-alpha"),
        )
        conn.execute(
            """
            INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, notes, source_path, cwd, last_used_at)
            VALUES (?, ?, ?, 'active', '', '', '', '2026-03-17T10:01:00+00:00')
            """,
            ("codex", "codex-session-123", "project-alpha"),
        )

    captured = {}

    def fake_execvpe(command, argv, env):
        captured["command"] = command
        captured["argv"] = argv
        captured["env"] = env
        raise SystemExit(0)

    _write_codex_session(fake_home, "codex-session-123", str(workspace_path))
    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr("os.execvpe", fake_execvpe)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "claude", "--project", "project-alpha"])

    try:
        main()
    except SystemExit as exc:
        assert exc.code == 0

    assert captured["argv"][1:] == ["--resume", "claude-session-abc"]
    assert captured["env"]["ASH_PROVIDER_SESSION_ID"] == "claude-session-abc"
    assert captured["env"]["ASH_CLAUDE_SESSION_ID"] == "claude-session-abc"
    assert captured["env"]["ASH_CODEX_SESSION_ID"] == "codex-session-123"
    assert captured["env"]["ASH_PROJECT_MEMORY_HINTS"] == ""


def test_cli_local_native_rejects_explicit_shared_project_without_path(monkeypatch, tmp_path, capsys) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    shared_db_path = tmp_path / "shared-projects.sqlite3"

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            """
        )
        conn.execute(
            "INSERT INTO projects (project_id, title, workspace_path, profile, summary) VALUES (?, ?, ?, '', '')",
            ("bad-project", "bad-project", ""),
        )

    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "claude", "--project", "bad-project"])

    exit_code = main()
    err = capsys.readouterr().err

    assert exit_code == 2
    assert "Workspace `bad-project` has no enterable path." in err


def test_cli_local_native_picker_hides_projects_without_path(monkeypatch, tmp_path, capsys) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    good_workspace_path = tmp_path / "good-project"
    good_workspace_path.mkdir()

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            """
        )
        conn.execute("INSERT INTO projects (project_id, title, workspace_path) VALUES (?, ?, ?)", ("bad-project", "bad-project", ""))
        conn.execute("INSERT INTO projects (project_id, title, workspace_path) VALUES (?, ?, ?)", ("good-project", "good-project", str(good_workspace_path)))

    captured = {}

    def fake_execvpe(command, argv, env):
        captured["env"] = env
        raise SystemExit(0)

    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setattr("builtins.input", lambda _prompt="": "1")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("os.execvpe", fake_execvpe)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex"])

    try:
        main()
    except SystemExit as exc:
        assert exc.code == 0

    output = capsys.readouterr().out
    assert "1. good-project" in output
    assert "bad-project" not in output
    assert captured["env"]["ASH_ACTIVE_WORKSPACE"] == "good-project"


def test_cli_local_native_picker_hides_projects_with_missing_directory(monkeypatch, tmp_path, capsys) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    good_workspace_path = tmp_path / "good-project"
    good_workspace_path.mkdir()
    missing_workspace_path = tmp_path / "missing-project"

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            """
        )
        conn.execute("INSERT INTO projects (project_id, title, workspace_path) VALUES (?, ?, ?)", ("missing-project", "missing-project", str(missing_workspace_path)))
        conn.execute("INSERT INTO projects (project_id, title, workspace_path) VALUES (?, ?, ?)", ("good-project", "good-project", str(good_workspace_path)))

    captured = {}

    def fake_execvpe(command, argv, env):
        captured["env"] = env
        raise SystemExit(0)

    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setattr("builtins.input", lambda _prompt="": "1")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("os.execvpe", fake_execvpe)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex"])

    try:
        main()
    except SystemExit as exc:
        assert exc.code == 0

    output = capsys.readouterr().out
    assert "1. good-project" in output
    assert "missing-project" not in output
    assert captured["env"]["ASH_ACTIVE_WORKSPACE"] == "good-project"


def test_cli_local_native_rejects_workspace_without_path(monkeypatch, tmp_path, capsys) -> None:
    db_path = tmp_path / "sessions.sqlite3"

    from agent_swarm_hub.session_store import SessionStore

    store = SessionStore(db_path)
    store.upsert_workspace(
        workspace_id="project-alpha",
        title="project-alpha",
        path="",
        backend="codex",
        transport="direct",
    )

    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex", "--project", "project-alpha"])

    exit_code = main()
    err = capsys.readouterr().err

    assert exit_code == 2
    assert "has no enterable path" in err
