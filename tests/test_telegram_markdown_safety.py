from agent_swarm_hub import TelegramConfig, TelegramRunner


def test_telegram_runner_defaults_to_plain_text_for_runtime_summaries() -> None:
    runner = TelegramRunner(TelegramConfig(enabled=True, bot_token="token"))

    result = runner.handle_update(
        {
            "message": {
                "message_id": 4,
                "text": "/write hello",
                "chat": {"id": 123},
                "from": {"id": 456},
            }
        }
    )

    assert result.outbound.parse_mode is None
    assert "in_progress" in result.outbound.text
