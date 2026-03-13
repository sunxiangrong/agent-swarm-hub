from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import urljoin

from .config import TelegramConfig
from .telegram import TelegramOutboundMessage


BASE_URL = "https://api.telegram.org"


@dataclass(frozen=True, slots=True)
class TelegramRequest:
    method: str
    url: str
    payload: dict


class TelegramTransport:
    """Pure request builder for Telegram Bot API interactions."""

    def __init__(self, config: TelegramConfig):
        self.config = config

    def build_send_message(self, outbound: TelegramOutboundMessage) -> TelegramRequest:
        return TelegramRequest(
            method="POST",
            url=self._method_url("sendMessage"),
            payload={
                "chat_id": outbound.chat_id,
                "text": outbound.text,
                **({"parse_mode": outbound.parse_mode} if outbound.parse_mode else {}),
                **({"reply_to_message_id": outbound.reply_to_message_id} if outbound.reply_to_message_id else {}),
            },
        )

    def build_get_updates(self, *, offset: int | None = None) -> TelegramRequest:
        payload = {
            "timeout": self.config.polling_timeout_s,
        }
        if offset is not None:
            payload["offset"] = offset
        return TelegramRequest(
            method="POST",
            url=self._method_url("getUpdates"),
            payload=payload,
        )

    def build_set_webhook(self) -> TelegramRequest:
        if not self.config.webhook_url:
            raise ValueError("ASH_TELEGRAM_WEBHOOK_URL is required to configure Telegram webhook mode")
        return TelegramRequest(
            method="POST",
            url=self._method_url("setWebhook"),
            payload={"url": self.config.webhook_url},
        )

    def _method_url(self, method: str) -> str:
        if not self.config.bot_token:
            raise ValueError("ASH_TELEGRAM_BOT_TOKEN is required for Telegram API requests")
        return urljoin(BASE_URL, f"/bot{self.config.bot_token}/{method}")

    @staticmethod
    def dumps_payload(request: TelegramRequest) -> str:
        return json.dumps(request.payload, ensure_ascii=False)
