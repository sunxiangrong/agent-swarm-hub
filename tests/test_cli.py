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
