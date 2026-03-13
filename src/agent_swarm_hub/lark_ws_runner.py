from __future__ import annotations

import json
from dataclasses import dataclass, field

import lark_oapi as lark

from .config import LarkConfig
from .lark_service import LarkDispatch, LarkService


@dataclass(slots=True)
class LarkWebSocketRunner:
    config: LarkConfig
    service: LarkService
    handled_dispatches: list[LarkDispatch] = field(default_factory=list)

    @classmethod
    def create(cls, config: LarkConfig, service: LarkService | None = None) -> "LarkWebSocketRunner":
        return cls(config=config, service=service or LarkService(config))

    def build_event_handler(self):
        return (
            lark.EventDispatcherHandler.builder("", self.config.verify_token)
            .register_p2_im_message_receive_v1(self._on_message_receive)
            .build()
        )

    def build_ws_client(self) -> lark.ws.Client:
        return lark.ws.Client(
            self.config.app_id,
            self.config.app_secret,
            log_level=lark.LogLevel.WARNING,
            event_handler=self.build_event_handler(),
            domain="https://open.larksuite.com",
        )

    def start(self) -> None:
        client = self.build_ws_client()
        client.start()

    def _on_message_receive(self, event) -> None:
        event_dict = self._event_to_dict(event)
        dispatch = self.service.handle_event(event_dict)
        self.handled_dispatches.append(dispatch)

        # Keep first-pass websocket handling lightweight so we stay within Lark's timeout budget.
        # Real send execution can be moved to a background worker in the daemon phase.
        self.service.send_outbound(dispatch.outbound)

    @staticmethod
    def _event_to_dict(event) -> dict:
        marshaled = lark.JSON.marshal(event)
        if not marshaled:
            raise ValueError("Lark websocket event could not be serialized")
        data = json.loads(marshaled)
        if not isinstance(data, dict):
            raise ValueError("Lark websocket event must serialize to an object")
        if "event" in data:
            return data
        return {"event": data}
