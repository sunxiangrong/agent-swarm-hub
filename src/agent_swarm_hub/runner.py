from __future__ import annotations

from dataclasses import dataclass

from .adapter import CCConnectAdapter
from .config import LarkConfig, TelegramConfig
from .lark import LarkOutboundMessage, build_lark_text_outbound, lark_event_to_remote_message
from .telegram import TelegramOutboundMessage, build_telegram_outbound, telegram_update_to_remote_message


@dataclass(frozen=True, slots=True)
class ChannelDispatchResult:
    platform: str
    outbound: TelegramOutboundMessage | LarkOutboundMessage
    task_id: str | None


class TelegramRunner:
    def __init__(self, config: TelegramConfig, adapter: CCConnectAdapter | None = None):
        self.config = config
        self.adapter = adapter or CCConnectAdapter()

    def handle_update(self, update: dict) -> ChannelDispatchResult:
        message = telegram_update_to_remote_message(update)
        response = self.adapter.handle_message(message)
        outbound = build_telegram_outbound(message, response.text)
        if self.config.default_parse_mode:
            outbound = TelegramOutboundMessage(
                chat_id=outbound.chat_id,
                text=outbound.text,
                parse_mode=self.config.default_parse_mode,
                reply_to_message_id=outbound.reply_to_message_id,
            )
        return ChannelDispatchResult(platform="telegram", outbound=outbound, task_id=response.task_id)


class LarkRunner:
    def __init__(self, config: LarkConfig, adapter: CCConnectAdapter | None = None):
        self.config = config
        self.adapter = adapter or CCConnectAdapter()

    def handle_event(self, event: dict) -> ChannelDispatchResult:
        message = lark_event_to_remote_message(event)
        response = self.adapter.handle_message(message)
        outbound = build_lark_text_outbound(message, response.text)
        return ChannelDispatchResult(platform="lark", outbound=outbound, task_id=response.task_id)
