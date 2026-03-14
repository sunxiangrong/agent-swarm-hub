from __future__ import annotations

import http.client
import json
import socket
import sys
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .telegram_service import TelegramDispatch, TelegramService
from .telegram_transport import TelegramRequest


@dataclass(frozen=True, slots=True)
class PollResult:
    updates_seen: int
    updates_processed: int
    next_offset: int | None


class TelegramPollingRunner:
    def __init__(self, service: TelegramService, *, retry_delay_s: float = 3.0):
        self.service = service
        self.retry_delay_s = retry_delay_s

    def run_once(self, *, offset: int | None = None) -> PollResult:
        poll_request = self.service.build_poll_request(offset=offset)
        poll_response = self._perform_json_request(poll_request)
        updates = poll_response.get("result") or []
        processed = 0
        next_offset = offset

        for update in updates:
            dispatch = self.service.handle_update(update)
            try:
                self._perform_json_request(dispatch.send_request)
            except RuntimeError as exc:
                if "message to be replied not found" not in str(exc).lower():
                    raise
                payload = dict(dispatch.send_request.payload)
                if "reply_to_message_id" not in payload:
                    raise
                payload.pop("reply_to_message_id", None)
                retry_request = TelegramRequest(
                    method=dispatch.send_request.method,
                    url=dispatch.send_request.url,
                    payload=payload,
                )
                self._perform_json_request(retry_request)
            processed += 1
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                next_offset = update_id + 1

        return PollResult(
            updates_seen=len(updates),
            updates_processed=processed,
            next_offset=next_offset,
        )

    def run_forever(self, *, offset: int | None = None) -> None:
        next_offset = offset
        while True:
            try:
                result = self.run_once(offset=next_offset)
            except RuntimeError as exc:
                if not self._is_retryable_error(exc):
                    raise
                print(f"[agent-swarm-hub] Telegram poll retrying after error: {exc}", file=sys.stderr, flush=True)
                time.sleep(self.retry_delay_s)
                continue
            next_offset = result.next_offset

    @staticmethod
    def _perform_json_request(request: TelegramRequest) -> dict[str, Any]:
        payload = json.dumps(request.payload, ensure_ascii=False).encode("utf-8")
        http_request = Request(
            request.url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method=request.method,
        )
        try:
            with urlopen(http_request, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            try:
                body = exc.read().decode("utf-8")
            except Exception:
                body = ""
            detail = f" body={body}" if body else ""
            raise RuntimeError(f"Telegram API HTTP error: {exc.code}{detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Telegram API connection error: {exc.reason}") from exc
        except (http.client.RemoteDisconnected, socket.timeout, TimeoutError, OSError) as exc:
            raise RuntimeError(f"Telegram API connection error: {exc}") from exc

        data = json.loads(raw)
        if not isinstance(data, dict):
            raise RuntimeError("Telegram API returned a non-object JSON payload")
        if not data.get("ok", False):
            raise RuntimeError(f"Telegram API error: {data}")
        return data

    @staticmethod
    def _is_retryable_error(exc: RuntimeError) -> bool:
        text = str(exc).lower()
        return "connection error" in text or "remote end closed connection" in text or "timed out" in text
