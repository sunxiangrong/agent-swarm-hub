from __future__ import annotations

from dataclasses import dataclass

from .remote import RemoteMessage, RemotePlatform


@dataclass(frozen=True, slots=True)
class TelegramOutboundMessage:
    chat_id: str
    text: str
    parse_mode: str | None = None
    reply_to_message_id: int | None = None


def telegram_update_to_remote_message(update: dict) -> RemoteMessage:
    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        raise ValueError("Telegram update does not contain a supported message payload")

    chat = message.get("chat") or {}
    from_user = message.get("from") or {}
    text = message.get("text") or message.get("caption") or ""
    message_id = message.get("message_id")
    thread_id = message.get("message_thread_id")

    return RemoteMessage(
        platform=RemotePlatform.TELEGRAM,
        chat_id=str(chat.get("id", "")),
        user_id=str(from_user.get("id", "")),
        text=str(text),
        thread_id=str(thread_id) if thread_id is not None else None,
        message_id=str(message_id) if message_id is not None else None,
    )


def build_telegram_outbound(message: RemoteMessage, text: str) -> TelegramOutboundMessage:
    reply_to_message_id: int | None = None
    if message.message_id and message.message_id.isdigit():
        reply_to_message_id = int(message.message_id)
    return TelegramOutboundMessage(
        chat_id=message.chat_id,
        text=text,
        reply_to_message_id=reply_to_message_id,
    )
