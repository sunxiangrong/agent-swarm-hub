from agent_swarm_hub.cli import main


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
    assert "has no configured path" in err
