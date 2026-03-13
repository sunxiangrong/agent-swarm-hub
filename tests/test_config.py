from agent_swarm_hub import RuntimeConfig


def test_runtime_config_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("ASH_TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("ASH_TELEGRAM_BOT_TOKEN", "tg-token")
    monkeypatch.setenv("ASH_LARK_ENABLED", "1")
    monkeypatch.setenv("ASH_LARK_APP_ID", "cli_app")
    monkeypatch.setenv("ASH_LARK_APP_SECRET", "cli_secret")

    config = RuntimeConfig.from_env()

    assert config.telegram.enabled is True
    assert config.telegram.bot_token == "tg-token"
    assert config.lark.enabled is True
    assert config.lark.app_id == "cli_app"
    assert config.lark.app_secret == "cli_secret"
