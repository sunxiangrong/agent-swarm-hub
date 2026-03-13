from __future__ import annotations

import json
from dataclasses import dataclass

from .remote import RemoteMessage, RemotePlatform


@dataclass(frozen=True, slots=True)
class LarkOutboundMessage:
    receive_id: str
    msg_type: str
    content: str


def lark_event_to_remote_message(event: dict) -> RemoteMessage:
    event_body = event.get("event") or {}
    message = event_body.get("message") or {}
    sender = event_body.get("sender") or {}
    sender_id = sender.get("sender_id") or {}

    content_raw = message.get("content") or "{}"
    content = _decode_lark_content(content_raw)
    text = content.get("text", "")
    chat_id = message.get("chat_id") or ""
    root_id = message.get("root_id")
    message_id = message.get("message_id")
    open_id = sender_id.get("open_id") or sender_id.get("user_id") or ""

    return RemoteMessage(
        platform=RemotePlatform.LARK,
        chat_id=str(chat_id),
        user_id=str(open_id),
        text=str(text),
        thread_id=str(root_id) if root_id else None,
        message_id=str(message_id) if message_id else None,
    )


def build_lark_text_outbound(message: RemoteMessage, text: str) -> LarkOutboundMessage:
    return LarkOutboundMessage(
        receive_id=message.chat_id,
        msg_type="text",
        content=json.dumps({"text": text}, ensure_ascii=False),
    )


def _decode_lark_content(raw: str) -> dict:
    try:
        content = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(content, dict):
        return {}
    return content
