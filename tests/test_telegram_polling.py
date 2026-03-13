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
