from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field

import lark_oapi as lark

from .config import LarkConfig
from .lark_service import LarkDispatch, LarkService


@dataclass(slots=True)
class LarkWebSocketRunner:
    config: LarkConfig
    service: LarkService
    handled_dispatches: list[LarkDispatch] = field(default_factory=list)
    reconnect_delay_s: float = 3.0

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
        print(
            f"[agent-swarm-hub] Lark WS starting app_id={self.config.app_id} "
            f"verify_token_configured={bool(self.config.verify_token)}",
            file=sys.stderr,
            flush=True,
        )
        client = self.build_ws_client()
        client.start()

    def run_forever(self) -> None:
        while True:
            try:
                self.start()
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"[agent-swarm-hub] Lark WS reconnecting after error: {exc}", file=sys.stderr, flush=True)
                time.sleep(self.reconnect_delay_s)
                continue
            print("[agent-swarm-hub] Lark WS exited unexpectedly, reconnecting", file=sys.stderr, flush=True)
            time.sleep(self.reconnect_delay_s)

    def _on_message_receive(self, event) -> None:
        event_dict = self._event_to_dict(event)
        message = ((event_dict.get("event") or {}).get("message") or {})
        sender = ((event_dict.get("event") or {}).get("sender") or {})
        sender_id = (sender.get("sender_id") or {}).get("open_id") or "unknown"
        chat_id = message.get("chat_id") or "unknown"
        print(
            f"[agent-swarm-hub] Lark event received sender={sender_id} chat={chat_id}",
            file=sys.stderr,
            flush=True,
        )
        dispatch = self.service.handle_event(event_dict)
        self.handled_dispatches.append(dispatch)
        print(
            f"[agent-swarm-hub] Lark dispatch task_id={dispatch.inbound_task_id} "
            f"receive_id={dispatch.outbound.receive_id}",
            file=sys.stderr,
            flush=True,
        )

        # Keep first-pass websocket handling lightweight so we stay within Lark's timeout budget.
        # Real send execution can be moved to a background worker in the daemon phase.
        self.service.send_outbound(dispatch.outbound)
        print(
            "[agent-swarm-hub] Lark reply sent",
            file=sys.stderr,
            flush=True,
        )

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
