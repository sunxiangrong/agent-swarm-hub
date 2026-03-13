from __future__ import annotations

import json
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
    def __init__(self, service: TelegramService):
        self.service = service

    def run_once(self, *, offset: int | None = None) -> PollResult:
        poll_request = self.service.build_poll_request(offset=offset)
        poll_response = self._perform_json_request(poll_request)
        updates = poll_response.get("result") or []
        processed = 0
        next_offset = offset

        for update in updates:
            dispatch = self.service.handle_update(update)
            self._perform_json_request(dispatch.send_request)
            processed += 1
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                next_offset = update_id + 1

        return PollResult(
            updates_seen=len(updates),
            updates_processed=processed,
            next_offset=next_offset,
        )

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
            raise RuntimeError(f"Telegram API HTTP error: {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"Telegram API connection error: {exc.reason}") from exc

        data = json.loads(raw)
        if not isinstance(data, dict):
            raise RuntimeError("Telegram API returned a non-object JSON payload")
        if not data.get("ok", False):
            raise RuntimeError(f"Telegram API error: {data}")
        return data
