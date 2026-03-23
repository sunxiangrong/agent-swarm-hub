from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from agent_swarm_hub.project_context import ProjectContextStore
from agent_swarm_hub.runtime_cleanup import run_runtime_cleanup
from agent_swarm_hub.session_store import SessionStore


def _fixed_now_ts() -> float:
    return datetime(2026, 3, 23, 12, 0, 0, tzinfo=timezone.utc).timestamp()


def test_runtime_cleanup_dry_run_skips_protected_tmux_sessions(monkeypatch, tmp_path) -> None:
    session_db = tmp_path / "runtime.sqlite3"
    project_db = tmp_path / "projects.sqlite3"
    session_store = SessionStore(session_db)
    project_store = ProjectContextStore(str(project_db))
    workspace_path = tmp_path / "active-project"
    workspace_path.mkdir()
    session_store.upsert_workspace(
        workspace_id="active-project",
        title="active-project",
        path=str(workspace_path),
        backend="codex",
        transport="auto",
    )
    session_store.upsert_workspace_session(
        session_key="local-cli",
        workspace_id="active-project",
        active_task_id="task-1",
        executor_session_id="exec-1",
        claude_session_id="claude-1",
        codex_session_id="codex-1",
        phase="executing",
        conversation_summary="running",
        swarm_state_json="{}",
        escalations_json="[]",
    )

    now_ts = _fixed_now_ts()
    created_old = int(now_ts - 3 * 60 * 60)

    class Result:
        def __init__(self, *, returncode: int, stdout: str = "", stderr: str = ""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(argv, check=False, capture_output=False, text=False):
        if argv[:2] == ["tmux", "list-sessions"]:
            return Result(
                returncode=0,
                stdout=(
                    f"ash-codex-active-project\t0\t{created_old}\n"
                    f"ash-codex-old-project\t0\t{created_old}\n"
                    f"ash-claude-old-project\t1\t{created_old}\n"
                ),
            )
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr("agent_swarm_hub.runtime_cleanup.subprocess.run", fake_run)
    monkeypatch.setattr("agent_swarm_hub.runtime_cleanup.repo_root", lambda: tmp_path)
    monkeypatch.setenv("ASH_CCB_RUN_DIR", str(tmp_path / "ccb"))

    report = run_runtime_cleanup(
        apply=False,
        session_store=session_store,
        project_store=project_store,
        tmux_grace_minutes=30,
        now_ts=now_ts,
    )

    actions = report["actions"]
    assert len(actions) == 1
    assert actions[0]["kind"] == "tmux_session_kill"
    assert actions[0]["target"] == "ash-codex-old-project"


def test_runtime_cleanup_apply_removes_stale_runtime_artifacts(monkeypatch, tmp_path) -> None:
    session_db = tmp_path / "runtime.sqlite3"
    project_db = tmp_path / "projects.sqlite3"
    session_store = SessionStore(session_db)
    project_store = ProjectContextStore(str(project_db))
    session_store.upsert_workspace(
        workspace_id="missing-project",
        title="missing-project",
        path=str(tmp_path / "missing-project"),
        backend="claude",
        transport="auto",
    )
    with sqlite3.connect(session_db) as conn:
        conn.execute(
            "UPDATE workspaces SET updated_at = ? WHERE workspace_id = ?",
            ("2020-01-01T00:00:00+00:00", "missing-project"),
        )

    pane_log = tmp_path / "var" / "panes" / "pane-logs" / "tmux" / "old.log"
    pane_log.parent.mkdir(parents=True, exist_ok=True)
    pane_log.write_text("old", encoding="utf-8")

    ccb_root = tmp_path / "ccb"
    ccb_root.mkdir(parents=True, exist_ok=True)
    ccb_file = ccb_root / "ccb-session-old.json"
    ccb_file.write_text("{}", encoding="utf-8")

    old_mtime = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(pane_log, (old_mtime, old_mtime))
    os.utime(ccb_file, (old_mtime, old_mtime))

    now_ts = _fixed_now_ts()
    created_old = int(now_ts - 3 * 60 * 60)
    calls: list[list[str]] = []

    class Result:
        def __init__(self, *, returncode: int, stdout: str = "", stderr: str = ""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(argv, check=False, capture_output=False, text=False):
        calls.append(argv)
        if argv[:2] == ["tmux", "list-sessions"]:
            return Result(returncode=0, stdout=f"ash-codex-dead\t0\t{created_old}\n")
        if argv[:3] == ["tmux", "kill-session", "-t"]:
            return Result(returncode=0)
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr("agent_swarm_hub.runtime_cleanup.subprocess.run", fake_run)
    monkeypatch.setattr("agent_swarm_hub.runtime_cleanup.repo_root", lambda: tmp_path)
    monkeypatch.setenv("ASH_CCB_RUN_DIR", str(ccb_root))

    report = run_runtime_cleanup(
        apply=True,
        session_store=session_store,
        project_store=project_store,
        tmux_grace_minutes=30,
        stale_workspace_days=1,
        pane_log_days=1,
        ccb_registry_days=1,
        now_ts=now_ts,
    )

    assert report["errors"] == 0
    assert any(item["kind"] == "tmux_session_kill" and item["applied"] for item in report["actions"])
    assert any(argv[:3] == ["tmux", "kill-session", "-t"] for argv in calls)
    assert session_store.get_workspace("missing-project") is None
    assert not pane_log.exists()
    assert not ccb_file.exists()


def test_runtime_cleanup_prunes_orphan_openviking_imports(monkeypatch, tmp_path) -> None:
    session_db = tmp_path / "runtime.sqlite3"
    project_db = tmp_path / "projects.sqlite3"
    session_store = SessionStore(session_db)
    project_store = ProjectContextStore(str(project_db))

    with sqlite3.connect(project_db) as conn:
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, '', '')
            """,
            ("keep-me", "keep-me", str(tmp_path / "keep-me")),
        )

    import_root = tmp_path / "imports" / "projects"
    keep_dir = import_root / "keep-me"
    stale_dir = import_root / "old-project"
    keep_dir.mkdir(parents=True, exist_ok=True)
    stale_dir.mkdir(parents=True, exist_ok=True)
    (stale_dir / "dummy.txt").write_text("old", encoding="utf-8")

    old_mtime = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
    os.utime(stale_dir, (old_mtime, old_mtime))

    class Result:
        returncode = 1
        stdout = ""
        stderr = "no server"

    monkeypatch.setattr(
        "agent_swarm_hub.runtime_cleanup.DEFAULT_IMPORT_TREE_ROOT",
        import_root,
    )
    monkeypatch.setattr("agent_swarm_hub.runtime_cleanup.subprocess.run", lambda *args, **kwargs: Result())
    monkeypatch.setattr("agent_swarm_hub.runtime_cleanup.repo_root", lambda: tmp_path)
    monkeypatch.setenv("ASH_CCB_RUN_DIR", str(tmp_path / "ccb"))

    report = run_runtime_cleanup(
        apply=True,
        session_store=session_store,
        project_store=project_store,
        prune_openviking_imports=True,
        openviking_import_days=1,
        now_ts=_fixed_now_ts(),
    )

    assert report["errors"] == 0
    assert keep_dir.exists()
    assert not stale_dir.exists()
