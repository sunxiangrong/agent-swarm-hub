import json

from agent_swarm_hub import CCConnectAdapter, EchoExecutor, LarkConfig, LarkRunner, RemoteMessage, RemotePlatform, SessionStore, TelegramConfig, TelegramRunner


def _bind_workspace(adapter: CCConnectAdapter, *, platform: RemotePlatform) -> None:
    response = adapter.handle_message(
        RemoteMessage(
            platform=platform,
            chat_id="123" if platform is RemotePlatform.TELEGRAM else "oc_1",
            user_id="456" if platform is RemotePlatform.TELEGRAM else "ou_1",
            text="/use project-alpha",
        )
    )
    assert "project-alpha" in response.text


def test_telegram_runner_dispatches_write_flow(tmp_path) -> None:
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=SessionStore(tmp_path / "sessions.sqlite3"))
    _bind_workspace(adapter, platform=RemotePlatform.TELEGRAM)
    runner = TelegramRunner(TelegramConfig(enabled=True, bot_token="token"), adapter=adapter)
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


def test_lark_runner_dispatches_status_flow(tmp_path) -> None:
    adapter = CCConnectAdapter(executor=EchoExecutor(), store=SessionStore(tmp_path / "sessions.sqlite3"))
    _bind_workspace(adapter, platform=RemotePlatform.LARK)
    runner = LarkRunner(LarkConfig(enabled=True, app_id="app", app_secret="secret"), adapter=adapter)
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
    text = json.loads(result.outbound.content)["text"]
    assert "Workspace: project-alpha" in text
    assert "Stage: pending" in text
