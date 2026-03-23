from pathlib import Path

from agent_swarm_hub.swarm_launch import cleanup_tmux_launch, ensure_orchestrator_pane


def test_ensure_orchestrator_pane_reuses_existing_tmux_pane(monkeypatch, tmp_path) -> None:
    workspace_path = tmp_path / "project-alpha"
    workspace_path.mkdir()

    class Result:
        returncode = 0
        stdout = "ash\t0\tmain\t%2\tash-chat | project-alpha | claude\t" + str(workspace_path) + "\n"
        stderr = ""

    calls = []

    def fake_run(argv, check=False, capture_output=False, text=False):
        calls.append(argv)
        return Result()

    monkeypatch.setattr("agent_swarm_hub.swarm_launch.subprocess.run", fake_run)

    result = ensure_orchestrator_pane(project_id="project-alpha", workspace_path=str(workspace_path))

    assert result["status"] == "existing"
    assert result["pane_id"] == "%2"
    assert result["launch_mode"] == "background"
    assert calls == [["tmux", "list-panes", "-a", "-F", "#{session_name}\t#{window_index}\t#{window_name}\t#{pane_id}\t#{pane_title}\t#{pane_current_path}"]]


def test_ensure_orchestrator_pane_launches_tmux_session_when_missing(monkeypatch, tmp_path) -> None:
    workspace_path = tmp_path / "project-alpha"
    workspace_path.mkdir()
    calls = []

    class ListResult:
        returncode = 0
        stdout = ""
        stderr = ""

    class LaunchResult:
        returncode = 0
        stdout = "ash-claude-project-alpha\t0\t%7\n"
        stderr = ""

    class ReListResult:
        returncode = 0
        stdout = "ash-claude-project-alpha\t0\tash-claude-project-alpha\t%7\tash-chat | project-alpha | claude\t" + str(workspace_path) + "\n"
        stderr = ""

    def fake_run(argv, check=False, capture_output=False, text=False):
        calls.append(argv)
        if argv[:3] == ["tmux", "list-panes", "-a"]:
            return ListResult() if len(calls) == 1 else ReListResult()
        return LaunchResult()

    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr("agent_swarm_hub.swarm_launch.subprocess.run", fake_run)

    result = ensure_orchestrator_pane(project_id="project-alpha", workspace_path=str(workspace_path))

    assert result["status"] == "launched"
    assert result["provider"] == "claude"
    assert result["session_name"] == "ash-claude-project-alpha"
    assert result["pane_id"] == "%7"
    assert result["launch_kind"] == "session"
    assert result["launch_mode"] == "background"
    assert calls[1][0:5] == ["tmux", "new-session", "-d", "-s", "ash-claude-project-alpha"]
    assert calls[1][5] == "-c"
    assert calls[1][6] == str(workspace_path)
    assert "-P" in calls[1]
    assert "-F" in calls[1]
    assert "ASH_AUTO_ENTER_NATIVE=1 ./scripts/start-chat.sh claude project-alpha" in calls[1][-1]


def test_cleanup_tmux_launch_kills_launched_session(monkeypatch) -> None:
    calls = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, check=False, capture_output=False, text=False):
        calls.append(argv)
        return Result()

    monkeypatch.setattr("agent_swarm_hub.swarm_launch.subprocess.run", fake_run)

    result = cleanup_tmux_launch(
        {
            "status": "launched",
            "launch_kind": "session",
            "session_name": "ash-claude-project-alpha",
            "window_index": "0",
            "pane_id": "%7",
        }
    )

    assert result["status"] == "cleaned"
    assert result["target"] == "ash-claude-project-alpha"
    assert calls == [["tmux", "kill-session", "-t", "ash-claude-project-alpha"]]


def test_ensure_orchestrator_pane_launches_window_keeps_target_without_relist(monkeypatch, tmp_path) -> None:
    workspace_path = tmp_path / "project-alpha"
    workspace_path.mkdir()
    calls = []

    class ListResult:
        returncode = 0
        stdout = ""
        stderr = ""

    class WindowLaunchResult:
        returncode = 0
        stdout = "ash\t4\t%9\n"
        stderr = ""

    def fake_run(argv, check=False, capture_output=False, text=False):
        calls.append(argv)
        if argv[:3] == ["tmux", "list-panes", "-a"]:
            return ListResult()
        return WindowLaunchResult()

    monkeypatch.setenv("TMUX", "1")
    monkeypatch.setattr("agent_swarm_hub.swarm_launch.subprocess.run", fake_run)

    result = ensure_orchestrator_pane(project_id="project-alpha", workspace_path=str(workspace_path), provider="codex")

    assert result["status"] == "launched"
    assert result["launch_kind"] == "window"
    assert result["session_name"] == "ash"
    assert result["window_index"] == "4"
    assert result["pane_id"] == "%9"
    assert calls[1][0:3] == ["tmux", "new-window", "-d"]
    assert "-P" in calls[1]
    assert "-F" in calls[1]


def test_cleanup_tmux_launch_falls_back_to_kill_pane_when_target_missing(monkeypatch) -> None:
    calls = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, check=False, capture_output=False, text=False):
        calls.append(argv)
        return Result()

    monkeypatch.setattr("agent_swarm_hub.swarm_launch.subprocess.run", fake_run)

    result = cleanup_tmux_launch(
        {
            "status": "launched",
            "launch_kind": "window",
            "session_name": "",
            "window_index": "",
            "pane_id": "%9",
        }
    )

    assert result["status"] == "cleaned"
    assert result["target"] == "%9"
    assert calls == [["tmux", "kill-pane", "-t", "%9"]]
