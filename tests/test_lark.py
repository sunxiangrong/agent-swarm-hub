import json

from agent_swarm_hub import (
    RemotePlatform,
    build_lark_text_outbound,
    lark_event_to_remote_message,
)


def test_lark_event_maps_im_message() -> None:
    remote = lark_event_to_remote_message(
        {
            "event": {
                "message": {
                    "message_id": "om_xxx",
                    "chat_id": "oc_xxx",
                    "root_id": "om_root",
                    "content": json.dumps({"text": "/write Draft a Lark rollout"}, ensure_ascii=False),
                },
                "sender": {
                    "sender_id": {
                        "open_id": "ou_xxx",
                    }
                },
            }
        }
    )

    assert remote.platform is RemotePlatform.LARK
    assert remote.chat_id == "oc_xxx"
    assert remote.user_id == "ou_xxx"
    assert remote.thread_id == "om_root"
    assert remote.text == "/write Draft a Lark rollout"


def test_lark_outbound_wraps_text_content() -> None:
    remote = lark_event_to_remote_message(
        {
            "event": {
                "message": {
                    "message_id": "om_123",
                    "chat_id": "oc_456",
                    "content": json.dumps({"text": "/status"}, ensure_ascii=False),
                },
                "sender": {
                    "sender_id": {
                        "open_id": "ou_789",
                    }
                },
            }
        }
    )

    outbound = build_lark_text_outbound(remote, "Task ID: abc123\nNo escalations so far.")

    assert outbound.receive_id == "oc_456"
    assert outbound.msg_type == "text"
    assert json.loads(outbound.content)["text"].startswith("Task ID: abc123")
