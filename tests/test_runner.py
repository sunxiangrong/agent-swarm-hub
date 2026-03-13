import json

from agent_swarm_hub import EchoExecutor, LarkConfig, LarkRunner, TelegramConfig, TelegramRunner


def test_telegram_runner_dispatches_write_flow() -> None:
    runner = TelegramRunner(TelegramConfig(enabled=True, bot_token="token"), adapter=None)
    runner.adapter.executor = EchoExecutor()

    result = runner.handle_update(
        {
            "message": {
                "message_id": 1,
                "text": "/write Draft runner integration",
                "chat": {"id": 123},
                "from": {"id": 456},
            }
        }
    )

    assert result.platform == "telegram"
    assert result.task_id is not None
    assert result.outbound.chat_id == "123"
    assert "Backend: echo" in result.outbound.text


def test_lark_runner_dispatches_status_flow() -> None:
    runner = LarkRunner(LarkConfig(enabled=True, app_id="app", app_secret="secret"), adapter=None)
    runner.adapter.executor = EchoExecutor()

    runner.handle_event(
        {
            "event": {
                "message": {
                    "message_id": "om_write",
                    "chat_id": "oc_1",
                    "content": json.dumps({"text": "/write Draft Lark runner"}, ensure_ascii=False),
                },
                "sender": {"sender_id": {"open_id": "ou_1"}},
            }
        }
    )
    result = runner.handle_event(
        {
            "event": {
                "message": {
                    "message_id": "om_status",
                    "chat_id": "oc_1",
                    "content": json.dumps({"text": "/status"}, ensure_ascii=False),
                },
                "sender": {"sender_id": {"open_id": "ou_1"}},
            }
        }
    )

    assert result.platform == "lark"
    assert result.task_id is not None
    assert json.loads(result.outbound.content)["text"].startswith("Task ID:")
