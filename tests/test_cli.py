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
