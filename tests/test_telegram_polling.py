from agent_swarm_hub.telegram_polling import TelegramPollingRunner


class _FakeTelegramService:
    def __init__(self):
        self.handled_updates = []

    def build_poll_request(self, *, offset=None):
        return type("Request", (), {"method": "POST", "url": "https://example.com/getUpdates", "payload": {"offset": offset}})()

    def handle_update(self, update):
        self.handled_updates.append(update)
        return type(
            "Dispatch",
            (),
            {
                "inbound_task_id": "task-1",
                "send_request": type(
                    "Request",
                    (),
                    {"method": "POST", "url": "https://example.com/sendMessage", "payload": {"text": "ok"}},
                )(),
            },
        )()


def test_polling_runner_processes_updates(monkeypatch) -> None:
    service = _FakeTelegramService()
    runner = TelegramPollingRunner(service)

    responses = iter(
        [
            {"ok": True, "result": [{"update_id": 10, "message": {"text": "/write hi"}}]},
            {"ok": True, "result": {"message_id": 1}},
        ]
    )

    monkeypatch.setattr(runner, "_perform_json_request", lambda request: next(responses))

    result = runner.run_once(offset=5)

    assert result.updates_seen == 1
    assert result.updates_processed == 1
    assert result.next_offset == 11
    assert service.handled_updates[0]["update_id"] == 10


def test_polling_runner_retries_without_reply_to_message_id(monkeypatch) -> None:
    service = _FakeTelegramService()
    runner = TelegramPollingRunner(service)

    requests = []
    responses = iter(
        [
            {"ok": True, "result": [{"update_id": 10, "message": {"text": "/write hi"}}]},
            RuntimeError('Telegram API HTTP error: 400 body={"ok":false,"description":"Bad Request: message to be replied not found"}'),
            {"ok": True, "result": {"message_id": 2}},
        ]
    )

    original_handle_update = service.handle_update

    def handle_update(update):
        dispatch = original_handle_update(update)
        dispatch.send_request.payload["reply_to_message_id"] = 99
        return dispatch

    def fake_perform(request):
        requests.append(request)
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    service.handle_update = handle_update
    monkeypatch.setattr(runner, "_perform_json_request", fake_perform)

    result = runner.run_once(offset=5)

    assert result.updates_processed == 1
    assert requests[1].payload["reply_to_message_id"] == 99
    assert "reply_to_message_id" not in requests[2].payload


def test_polling_runner_retries_transient_connection_errors(monkeypatch) -> None:
    service = _FakeTelegramService()
    runner = TelegramPollingRunner(service, retry_delay_s=0)

    calls = {"count": 0}

    def fake_run_once(*, offset=None):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("Telegram API connection error: Remote end closed connection without response")
        if calls["count"] == 2:
            return type("Result", (), {"next_offset": 11})()
        raise KeyboardInterrupt()

    monkeypatch.setattr(runner, "run_once", fake_run_once)
    monkeypatch.setattr("time.sleep", lambda *_args, **_kwargs: None)

    try:
        runner.run_forever(offset=5)
    except KeyboardInterrupt:
        pass

    assert calls["count"] == 3
