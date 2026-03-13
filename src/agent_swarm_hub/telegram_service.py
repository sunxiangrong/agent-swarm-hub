from __future__ import annotations

from dataclasses import dataclass

from .config import TelegramConfig
from .runner import ChannelDispatchResult, TelegramRunner
from .telegram_transport import TelegramRequest, TelegramTransport


@dataclass(frozen=True, slots=True)
class TelegramDispatch:
    inbound_task_id: str | None
    send_request: TelegramRequest


class TelegramService:
    """Bridge Telegram runtime handling to transport requests."""

    def __init__(self, config: TelegramConfig, runner: TelegramRunner | None = None):
        self.config = config
        self.runner = runner or TelegramRunner(config)
        self.transport = TelegramTransport(config)

    def handle_update(self, update: dict) -> TelegramDispatch:
        result: ChannelDispatchResult = self.runner.handle_update(update)
        request = self.transport.build_send_message(result.outbound)
        return TelegramDispatch(inbound_task_id=result.task_id, send_request=request)

    def build_poll_request(self, *, offset: int | None = None) -> TelegramRequest:
        return self.transport.build_get_updates(offset=offset)

    def build_set_webhook_request(self) -> TelegramRequest:
        return self.transport.build_set_webhook()
