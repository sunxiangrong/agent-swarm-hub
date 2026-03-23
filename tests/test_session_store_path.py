from __future__ import annotations

import sqlite3

from agent_swarm_hub.project_context import ProjectContextStore
from agent_swarm_hub.session_store import SessionStore


def test_session_store_relative_db_path_survives_cwd_changes(monkeypatch, tmp_path) -> None:
    first_cwd = tmp_path / "first"
    second_cwd = tmp_path / "second"
    first_cwd.mkdir()
    second_cwd.mkdir()

    monkeypatch.chdir(first_cwd)
    store = SessionStore("var/db/runtime.sqlite3")
    db_path = store.db_path
    assert db_path.is_absolute()
    assert db_path == (first_cwd / "var" / "db" / "runtime.sqlite3").resolve()

    monkeypatch.chdir(second_cwd)
    store.upsert_workspace(
        workspace_id="demo",
        title="demo",
        path=str(tmp_path / "demo"),
        backend="codex",
        transport="auto",
    )
    workspace = store.get_workspace("demo")
    assert workspace is not None
    assert store.db_path == db_path
    assert db_path.exists()


def test_project_context_promotes_global_memory_and_writes_shared_file(tmp_path) -> None:
    db_path = tmp_path / "shared-projects.sqlite3"
    workspace = tmp_path / "demo"
    workspace.mkdir()

    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (
                project_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                workspace_path TEXT NOT NULL DEFAULT '',
                profile TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "demo",
                "demo",
                str(workspace),
                "",
                "Project: demo\nCurrent focus: 本地默认不要持续 OV 同步\nRecent context: 服务器上可以持续同步",
            ),
        )

    store = ProjectContextStore(str(db_path))
    promoted = store.promote_project_memory_to_global(
        "demo",
        memory="本地默认不要持续 OV 同步，服务器上可以持续同步。",
        recent_hints=["代理默认走 6789"],
    )

    snapshot = store.build_global_memory_snapshot()
    shared_path = store.sync_global_memory_file()

    assert promoted >= 1
    assert "OV" in snapshot["summary"] or "ov" in snapshot["summary"].lower()
    assert shared_path is not None
    assert shared_path.exists()
    content = shared_path.read_text(encoding="utf-8")
    assert "本地默认不要持续 OV 同步" in content
