import json

from agent_swarm_hub import LarkConfig, LarkService


def test_lark_service_builds_create_message_request() -> None:
    service = LarkService(
        LarkConfig(enabled=True, app_id="app", app_secret="secret"),
    )

    request = service.build_create_message_request(
        outbound=type(
            "Outbound",
            (),
            {
                "receive_id": "oc_123",
                "msg_type": "text",
                "content": json.dumps({"text": "hello"}, ensure_ascii=False),
            },
        )()
    )

    assert request.receive_id_type == "chat_id"
    assert request.request_body.receive_id == "oc_123"
    assert request.request_body.msg_type == "text"


def test_lark_service_handles_challenge() -> None:
    service = LarkService(LarkConfig(enabled=True, app_id="app", app_secret="secret"))

    response = service.challenge_response({"challenge": "abc123"})

    assert response == {"challenge": "abc123"}


def test_lark_service_dispatches_event_to_outbound_text() -> None:
    service = LarkService(LarkConfig(enabled=True, app_id="app", app_secret="secret"))

    dispatch = service.handle_event(
        {
            "event": {
                "message": {
                    "message_id": "om_123",
                    "chat_id": "oc_456",
                    "content": json.dumps({"text": "/write hello from lark"}, ensure_ascii=False),
                },
                "sender": {"sender_id": {"open_id": "ou_789"}},
            }
        }
    )

    assert dispatch.inbound_task_id is not None
    assert dispatch.outbound.receive_id == "oc_456"
    assert json.loads(dispatch.outbound.content)["text"].startswith("Accepted task.")
