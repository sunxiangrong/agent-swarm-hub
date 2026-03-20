from pathlib import Path

from agent_swarm_hub.swarm_launch import ensure_orchestrator_pane


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
        stdout = ""
        stderr = ""

    def fake_run(argv, check=False, capture_output=False, text=False):
        calls.append(argv)
        if argv[:3] == ["tmux", "list-panes", "-a"]:
            return ListResult()
        return LaunchResult()

    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.setattr("agent_swarm_hub.swarm_launch.subprocess.run", fake_run)

    result = ensure_orchestrator_pane(project_id="project-alpha", workspace_path=str(workspace_path))

    assert result["status"] == "launched"
    assert result["provider"] == "claude"
    assert result["session_name"] == "ash-orch-project-alpha"
    assert calls[1][0:5] == ["tmux", "new-session", "-d", "-s", "ash-orch-project-alpha"]
    assert calls[1][5] == "-c"
    assert calls[1][6] == str(workspace_path)
