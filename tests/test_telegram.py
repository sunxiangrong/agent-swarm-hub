from agent_swarm_hub import (
    RemotePlatform,
    build_telegram_outbound,
    telegram_update_to_remote_message,
)


def test_telegram_update_maps_basic_message() -> None:
    remote = telegram_update_to_remote_message(
        {
            "update_id": 1,
            "message": {
                "message_id": 12,
                "text": "/write Draft a Telegram bot flow",
                "chat": {"id": 345},
                "from": {"id": 678},
            },
        }
    )

    assert remote.platform is RemotePlatform.TELEGRAM
    assert remote.chat_id == "345"
    assert remote.user_id == "678"
    assert remote.text == "/write Draft a Telegram bot flow"
    assert remote.message_id == "12"


def test_telegram_outbound_replies_to_source_message_when_available() -> None:
    remote = telegram_update_to_remote_message(
        {
            "update_id": 2,
            "message": {
                "message_id": 88,
                "text": "/status",
                "chat": {"id": -100123},
                "from": {"id": 901},
            },
        }
    )

    outbound = build_telegram_outbound(remote, "Task ID: abc123\nNo escalations so far.")

    assert outbound.chat_id == "-100123"
    assert outbound.reply_to_message_id == 88
    assert "Task ID: abc123" in outbound.text
