from __future__ import annotations

from agent_swarm_hub.runtime_monitor import run_runtime_monitor


def test_runtime_monitor_runs_heartbeat_and_auto_continue(monkeypatch) -> None:
    heartbeats: list[tuple[str | None, bool, bool]] = []
    auto_runs: list[str] = []
    sleeps: list[float] = []

    class FakeProject:
        def __init__(self, project_id: str):
            self.project_id = project_id

    class FakeStore:
        def list_projects(self):
            return [FakeProject("alpha"), FakeProject("beta")]

        def record_auto_continue_state(self, project_id: str, provider: str, *, status: str, summary: str = "", details=None):
            return None

    monkeypatch.setattr("agent_swarm_hub.runtime_monitor.ProjectContextStore", FakeStore)

    def fake_heartbeat(project_id: str | None, monitor_all: bool, apply: bool) -> int:
        heartbeats.append((project_id, monitor_all, apply))
        return 0

    def fake_plan(project_id: str, *, context_store=None):
        if project_id == "alpha":
            return {"code": 0, "prompt": "do one step"}
        return {"code": 0, "message": "No auto-continue candidate is available.", "prompt": ""}

    def fake_auto_continue(project_id: str, *, provider, explain, sync_project_memory_artifacts_cb):
        auto_runs.append(project_id)
        return 0

    monkeypatch.setattr("agent_swarm_hub.runtime_monitor.build_auto_continue_plan", fake_plan)
    monkeypatch.setattr("agent_swarm_hub.runtime_monitor.project_sessions_auto_continue", fake_auto_continue)
    monkeypatch.setattr(
        "agent_swarm_hub.runtime_monitor.evaluate_auto_continue_completion",
        lambda project_id, *, provider, context_store=None: {
            "status": "active",
            "reason": "still progressing",
            "next_step": "continue",
            "blocker": "",
            "needs_confirmation": False,
            "provider": "codex",
            "backend": "codex",
        },
    )
    monkeypatch.setattr("agent_swarm_hub.runtime_monitor.time.sleep", lambda seconds: sleeps.append(seconds))

    exit_code = run_runtime_monitor(
        project_id=None,
        monitor_all=True,
        apply=True,
        auto_continue_enabled=True,
        until_complete=False,
        interval_seconds=5.0,
        cycles=2,
        heartbeat_cb=fake_heartbeat,
        sync_project_memory_artifacts_cb=lambda store, pid: None,
    )

    assert exit_code == 0
    assert heartbeats == [(None, True, True), (None, True, True)]
    assert auto_runs == ["alpha", "alpha"]
    assert sleeps == [5.0]


def test_runtime_monitor_until_complete_stops_when_no_candidates(monkeypatch) -> None:
    heartbeats: list[tuple[str | None, bool, bool]] = []
    sleeps: list[float] = []
    recorded: list[tuple[str, str, str]] = []

    class FakeProject:
        def __init__(self, project_id: str):
            self.project_id = project_id

    class FakeStore:
        def list_projects(self):
            return [FakeProject("alpha")]

        def record_auto_continue_state(self, project_id: str, provider: str, *, status: str, summary: str = "", details=None):
            recorded.append((project_id, provider, status))

    monkeypatch.setattr("agent_swarm_hub.runtime_monitor.ProjectContextStore", FakeStore)

    def fake_heartbeat(project_id: str | None, monitor_all: bool, apply: bool) -> int:
        heartbeats.append((project_id, monitor_all, apply))
        return 0

    monkeypatch.setattr(
        "agent_swarm_hub.runtime_monitor.build_auto_continue_plan",
        lambda project_id, *, context_store=None: {"code": 0, "message": "No auto-continue candidate is available.", "prompt": ""},
    )
    monkeypatch.setattr("agent_swarm_hub.runtime_monitor.time.sleep", lambda seconds: sleeps.append(seconds))

    exit_code = run_runtime_monitor(
        project_id="alpha",
        monitor_all=False,
        apply=True,
        auto_continue_enabled=True,
        until_complete=True,
        interval_seconds=5.0,
        cycles=5,
        heartbeat_cb=fake_heartbeat,
        sync_project_memory_artifacts_cb=lambda store, pid: None,
    )

    assert exit_code == 0
    assert heartbeats == [("alpha", False, True)]
    assert recorded == [("alpha", "codex", "settled")]
    assert sleeps == []


def test_runtime_monitor_until_complete_stops_on_terminal_completion(monkeypatch) -> None:
    heartbeats: list[tuple[str | None, bool, bool]] = []
    auto_runs: list[str] = []
    recorded: list[tuple[str, str, str, str]] = []
    sleeps: list[float] = []

    class FakeStore:
        def record_auto_continue_state(self, project_id: str, provider: str, *, status: str, summary: str = "", details=None):
            recorded.append((project_id, provider, status, summary))

    monkeypatch.setattr("agent_swarm_hub.runtime_monitor.ProjectContextStore", FakeStore)

    def fake_heartbeat(project_id: str | None, monitor_all: bool, apply: bool) -> int:
        heartbeats.append((project_id, monitor_all, apply))
        return 0

    monkeypatch.setattr(
        "agent_swarm_hub.runtime_monitor.build_auto_continue_plan",
        lambda project_id, *, context_store=None: {"code": 0, "prompt": "do one step"},
    )

    def fake_auto_continue(project_id: str, *, provider, explain, sync_project_memory_artifacts_cb):
        auto_runs.append(project_id)
        return 0

    monkeypatch.setattr("agent_swarm_hub.runtime_monitor.project_sessions_auto_continue", fake_auto_continue)
    monkeypatch.setattr(
        "agent_swarm_hub.runtime_monitor.evaluate_auto_continue_completion",
        lambda project_id, *, provider, context_store=None: {
            "status": "completed",
            "reason": "Task reached a stable stopping point.",
            "next_step": "",
            "blocker": "",
            "needs_confirmation": False,
            "provider": "codex",
            "backend": "codex",
        },
    )
    monkeypatch.setattr("agent_swarm_hub.runtime_monitor.time.sleep", lambda seconds: sleeps.append(seconds))

    exit_code = run_runtime_monitor(
        project_id="alpha",
        monitor_all=False,
        apply=True,
        auto_continue_enabled=True,
        until_complete=True,
        interval_seconds=5.0,
        cycles=5,
        heartbeat_cb=fake_heartbeat,
        sync_project_memory_artifacts_cb=lambda store, pid: None,
    )

    assert exit_code == 0
    assert heartbeats == [("alpha", False, True)]
    assert auto_runs == ["alpha"]
    assert recorded == [("alpha", "codex", "completed", "Task reached a stable stopping point.")]
    assert sleeps == []
