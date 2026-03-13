from agent_swarm_hub import EchoExecutor, TelegramConfig, TelegramService, TelegramTransport


def test_telegram_transport_builds_send_message_request() -> None:
    transport = TelegramTransport(
        TelegramConfig(enabled=True, bot_token="123:abc", default_parse_mode="")
    )

    request = transport.build_send_message(
        outbound=type(
            "Outbound",
            (),
            {
                "chat_id": "42",
                "text": "hello",
                "parse_mode": None,
                "reply_to_message_id": 7,
            },
        )()
    )

    assert request.method == "POST"
    assert request.url.endswith("/bot123:abc/sendMessage")
    assert request.payload["chat_id"] == "42"
    assert request.payload["reply_to_message_id"] == 7
    assert "parse_mode" not in request.payload


def test_telegram_service_turns_update_into_send_request() -> None:
    service = TelegramService(
        TelegramConfig(enabled=True, bot_token="999:xyz", default_parse_mode="")
    )
    service.runner.adapter.executor = EchoExecutor()

    dispatch = service.handle_update(
        {
            "message": {
                "message_id": 3,
                "text": "/write Draft actual telegram wiring",
                "chat": {"id": 1001},
                "from": {"id": 2002},
            }
        }
    )

    assert dispatch.inbound_task_id is not None
    assert dispatch.send_request.url.endswith("/bot999:xyz/sendMessage")
    assert "Backend: echo" in dispatch.send_request.payload["text"]


def test_telegram_transport_builds_webhook_request() -> None:
    transport = TelegramTransport(
        TelegramConfig(
            enabled=True,
            bot_token="123:abc",
            default_parse_mode="",
            webhook_url="https://example.com/telegram/webhook",
        )
    )

    request = transport.build_set_webhook()

    assert request.url.endswith("/bot123:abc/setWebhook")
    assert request.payload["url"] == "https://example.com/telegram/webhook"
