from __future__ import annotations

import json
from dataclasses import dataclass

import lark_oapi as lark
from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

from .config import LarkConfig
from .lark import LarkOutboundMessage
from .runner import ChannelDispatchResult, LarkRunner


@dataclass(frozen=True, slots=True)
class LarkDispatch:
    inbound_task_id: str | None
    outbound: LarkOutboundMessage


class LarkService:
    """Bridge Lark runtime handling to the official SDK request flow."""

    def __init__(
        self,
        config: LarkConfig,
        runner: LarkRunner | None = None,
        client: lark.Client | None = None,
    ):
        self.config = config
        self.runner = runner or LarkRunner(config)
        self.client = client or self._build_client(config)

    def handle_event(self, event: dict) -> LarkDispatch:
        result: ChannelDispatchResult = self.runner.handle_event(event)
        return LarkDispatch(inbound_task_id=result.task_id, outbound=result.outbound)

    def build_create_message_request(self, outbound: LarkOutboundMessage) -> CreateMessageRequest:
        body = (
            CreateMessageRequestBody.builder()
            .receive_id(outbound.receive_id)
            .msg_type(outbound.msg_type)
            .content(outbound.content)
            .build()
        )
        return (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(body)
            .build()
        )

    def send_outbound(self, outbound: LarkOutboundMessage):
        request = self.build_create_message_request(outbound)
        return self.client.im.v1.message.create(request)

    def challenge_response(self, event: dict) -> dict | None:
        challenge = event.get("challenge")
        if challenge:
            return {"challenge": challenge}
        return None

    @staticmethod
    def decode_event_payload(raw: str) -> dict:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Lark event payload must decode to an object")
        return data

    @staticmethod
    def _build_client(config: LarkConfig) -> lark.Client:
        return (
            lark.Client.builder()
            .app_id(config.app_id)
            .app_secret(config.app_secret)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )
