from unittest.mock import patch

from agent_swarm_hub import LarkConfig, LarkWebSocketRunner


class _FakeService:
    def __init__(self):
        self.events = []
        self.sent = []

    def handle_event(self, event: dict):
        self.events.append(event)
        return type(
            "Dispatch",
            (),
            {
                "inbound_task_id": "task-1",
                "outbound": type(
                    "Outbound",
                    (),
                    {
                        "receive_id": "oc_123",
                        "msg_type": "text",
                        "content": '{"text":"ok"}',
                    },
                )(),
            },
        )()

    def send_outbound(self, outbound):
        self.sent.append(outbound)


class _FakeEvent:
    def __init__(self, payload: dict):
        self.payload = payload


def test_lark_ws_runner_wraps_marshaled_event() -> None:
    service = _FakeService()
    runner = LarkWebSocketRunner.create(
        LarkConfig(enabled=True, app_id="app", app_secret="secret", verify_token="verify"),
        service=service,
    )

    event = _FakeEvent(
        {
            "message": {
                "message_id": "om_1",
                "chat_id": "oc_1",
                "content": "{\"text\":\"/write hello from ws\"}",
            },
            "sender": {
                "sender_id": {
                    "open_id": "ou_1",
                }
            },
        }
    )

    with patch("agent_swarm_hub.lark_ws_runner.lark.JSON.marshal", return_value='{"message":{"message_id":"om_1","chat_id":"oc_1","content":"{\\"text\\":\\"/write hello from ws\\"}"},"sender":{"sender_id":{"open_id":"ou_1"}}}'):
        runner._on_message_receive(event)

    assert service.events[0]["event"]["message"]["chat_id"] == "oc_1"
    assert service.sent[0].receive_id == "oc_123"


def test_lark_ws_runner_builds_handler() -> None:
    runner = LarkWebSocketRunner.create(
        LarkConfig(enabled=True, app_id="app", app_secret="secret", verify_token="verify")
    )

    handler = runner.build_event_handler()

    assert handler is not None
