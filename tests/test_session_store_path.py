from __future__ import annotations

import sqlite3

from agent_swarm_hub.bridge_policy import (
    bridge_policy_env,
    bridge_policy_path,
    default_bridge_policy,
    init_bridge_policy,
    load_bridge_policy,
    update_bridge_policy,
)
from agent_swarm_hub.executor import ExecutionResult, Executor
from agent_swarm_hub.native_entry import inject_project_memory_env
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


def test_project_context_ai_global_memory_candidates_are_filtered_and_promoted(tmp_path) -> None:
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
            ("demo", "demo", str(workspace), "", "Project: demo"),
        )

    class FakeExecutor(Executor):
        def run(self, prompt: str) -> ExecutionResult:
            return ExecutionResult(
                output="""
                {
                  "focus": "keep runtime behavior stable",
                  "current_state": "memory rules are being consolidated",
                  "next_step": "verify global promotion",
                  "long_term_memory": "Use conservative global memory promotion with explicit filtering.",
                  "key_points": ["Proxy should default to 6789 locally."],
                  "global_memory_candidates": [
                    {
                      "content": "Local macOS sessions should disable continuous OpenViking auto sync by default.",
                      "category": "environment",
                      "confidence": 0.93,
                      "reason": "cross-project local-machine default"
                    },
                    {
                      "content": "The demo project should always use the /Users/demo/project path.",
                      "category": "workflow",
                      "confidence": 0.91,
                      "reason": "project-specific path"
                    }
                  ]
                }
                """,
                backend="echo",
                strategy="test",
            )

    store = ProjectContextStore(str(db_path))
    assert store.consolidate_project_memory(
        "demo",
        live_summary="Local defaults matter more than project-specific paths.",
        recent_messages=["user: 本地默认不要持续 OV 同步", "assistant: 代理默认走 6789"],
        executor=FakeExecutor(),
    )

    rows = store.list_global_memory(limit=8)
    contents = [str(row.get("content") or "") for row in rows]

    assert any("OpenViking auto sync" in item for item in contents)
    assert not any("/Users/demo/project" in item for item in contents)
    assert not any("demo project" in item for item in contents)


def test_project_context_can_disable_ai_global_memory_candidates(monkeypatch, tmp_path) -> None:
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
            ("demo", "demo", str(workspace), "", "Project: demo"),
        )

    class FakeExecutor(Executor):
        def run(self, prompt: str) -> ExecutionResult:
            return ExecutionResult(
                output="""
                {
                  "focus": "stable local workflow",
                  "current_state": "checking memory promotion",
                  "next_step": "verify AI candidate toggle",
                  "long_term_memory": "Keep normal project memory working.",
                  "key_points": [],
                  "global_memory_candidates": [
                    {
                      "content": "Local macOS sessions should disable continuous OpenViking auto sync by default.",
                      "category": "environment",
                      "confidence": 0.93,
                      "reason": "cross-project local-machine default"
                    }
                  ]
                }
                """,
                backend="echo",
                strategy="test",
            )

    monkeypatch.setenv("ASH_ENABLE_AI_GLOBAL_MEMORY_CANDIDATES", "0")
    store = ProjectContextStore(str(db_path))
    assert store.consolidate_project_memory(
        "demo",
        live_summary="Normal project memory consolidation still works.",
        recent_messages=["user: keep project memory", "assistant: do not promote AI global candidates"],
        executor=FakeExecutor(),
    )


def test_bridge_policy_init_and_env_render(tmp_path) -> None:
    workspace = tmp_path / "demo"
    workspace.mkdir()

    policy_path = init_bridge_policy("demo", str(workspace))
    assert policy_path == bridge_policy_path(workspace)
    assert policy_path.exists()

    policy = load_bridge_policy("demo", str(workspace))
    env = bridge_policy_env(policy)

    assert policy.project_id == "demo"
    assert "manual" in policy.readable_targets
    assert "ssh:xinong" in policy.writable_targets
    assert env["TMUX_BRIDGE_WRITABLE_PATHS"] == str(workspace)
    assert "sudo" in env["TMUX_BRIDGE_DENY_PREFIXES"]


def test_bridge_policy_load_uses_defaults_when_file_missing(tmp_path) -> None:
    workspace = tmp_path / "demo"
    workspace.mkdir()

    policy = load_bridge_policy("demo", str(workspace))
    default = default_bridge_policy("demo", str(workspace))

    assert policy == default


def test_bridge_policy_update_syncs_readable_and_writable_targets(tmp_path) -> None:
    workspace = tmp_path / "demo"
    workspace.mkdir()

    init_bridge_policy("demo", str(workspace))
    updated = update_bridge_policy("demo", str(workspace), ssh_targets=["xinong", "gpu"])

    assert updated.ssh_targets == ["xinong", "gpu"]
    assert "ssh:xinong" in updated.readable_targets
    assert "ssh:gpu" in updated.readable_targets
    assert "ssh:ias" not in updated.readable_targets
    assert "ssh:gpu" in updated.writable_targets
    assert "ssh:ias" not in updated.writable_targets


def test_project_context_ai_only_mode_skips_rule_based_promotion(monkeypatch, tmp_path) -> None:
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
            ("demo", "demo", str(workspace), "", "Project: demo"),
        )

    monkeypatch.setenv("ASH_GLOBAL_MEMORY_PROMOTION_MODE", "ai-only")
    store = ProjectContextStore(str(db_path))
    promoted = store.promote_project_memory_to_global(
        "demo",
        memory="本地默认不要持续 OV 同步，服务器上可以持续同步。",
        recent_hints=["代理默认走 6789"],
        ai_candidates=[],
    )

    rows = store.list_global_memory(limit=8)
    assert promoted == 0
    assert rows == []


def test_project_context_prunes_noisy_global_memory_rows(tmp_path) -> None:
    db_path = tmp_path / "shared-projects.sqlite3"
    store = ProjectContextStore(str(db_path))
    assert store.upsert_global_memory(
        content="Task ID: 801e8dbd2992 Phase: discussion Backend: claude",
        source_project_id="agent-swarm-hub",
        confidence=0.8,
    )
    assert store.upsert_global_memory(
        content="Local macOS sessions should disable continuous OpenViking auto sync by default.",
        source_project_id="agent-swarm-hub",
        confidence=0.9,
    )

    pruned = store.prune_global_memory()
    rows = store.list_global_memory(limit=8)
    contents = [str(row.get("content") or "") for row in rows]

    assert pruned == 1
    assert any("OpenViking auto sync" in item for item in contents)
    assert not any("Task ID:" in item for item in contents)


def test_project_context_scoped_shared_memory_injection(tmp_path) -> None:
    db_path = tmp_path / "shared-projects.sqlite3"
    workspace_a = tmp_path / "project-a"
    workspace_b = tmp_path / "project-b"
    workspace_a.mkdir()
    workspace_b.mkdir()

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
        conn.executemany(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                ("project-a", "project-a", str(workspace_a), "", "Project: project-a"),
                ("project-b", "project-b", str(workspace_b), "", "Project: project-b"),
            ],
        )

    store = ProjectContextStore(str(db_path))
    assert store.bind_project_memory_scope("project-a", "nwafu")
    assert store.upsert_global_memory(
        scope="shared:nwafu",
        content="NWAFU server login and scheduler rules are shared between related bioinfo projects.",
        source_project_id="project-a",
        confidence=0.9,
    )
    assert store.upsert_global_memory(
        scope="global",
        content="Local macOS sessions should disable continuous OpenViking auto sync by default.",
        source_project_id="project-a",
        confidence=0.95,
    )

    snapshot_a = store.build_memory_snapshot(str(workspace_a))
    snapshot_b = store.build_memory_snapshot(str(workspace_b))
    env_a: dict[str, str] = {}
    env_b: dict[str, str] = {}

    assert "shared:nwafu" in snapshot_a["shared_scopes"]
    assert "NWAFU server login" in snapshot_a["shared_memory"]
    assert "OpenViking auto sync" in snapshot_a["global_memory"]
    assert snapshot_b["shared_memory"] == ""
    assert "OpenViking auto sync" in snapshot_b["global_memory"]

    assert inject_project_memory_env(env_a, workspace_path=str(workspace_a), context_store=store, read_openviking_overview_cb=lambda _uri: "")
    assert inject_project_memory_env(env_b, workspace_path=str(workspace_b), context_store=store, read_openviking_overview_cb=lambda _uri: "")

    assert "shared:nwafu" in env_a["ASH_SHARED_MEMORY_SCOPES"]
    assert "NWAFU server login" in env_a["ASH_SHARED_MEMORY_SUMMARY"]
    assert env_b["ASH_SHARED_MEMORY_SUMMARY"] == ""


def test_project_context_default_scopes_and_knowledge_system_sees_all_shared_scopes(tmp_path) -> None:
    db_path = tmp_path / "shared-projects.sqlite3"
    workspace_a = tmp_path / "cell_qtl"
    workspace_b = tmp_path / "knowledge-system"
    workspace_a.mkdir()
    workspace_b.mkdir()

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
        conn.executemany(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                ("cell_qtl", "cell_qtl", str(workspace_a), "", "Project: cell_qtl"),
                ("knowledge-system", "knowledge-system", str(workspace_b), "", "Project: knowledge-system"),
            ],
        )

    store = ProjectContextStore(str(db_path))
    added = store.ensure_default_memory_scopes_for_all_projects()
    assert added >= 2
    assert "shared:all-projects" in store.resolve_project_memory_scopes("cell_qtl")
    assert "shared:bioinfo" in store.resolve_project_memory_scopes("cell_qtl")

    assert store.upsert_global_memory(
        scope="shared:nwafu",
        content="NWAFU cluster access rules are shared by server-based projects.",
        source_project_id="cell_qtl",
        confidence=0.9,
    )
    assert "shared:nwafu" in store.resolve_project_memory_scopes("knowledge-system")

    knowledge_snapshot = store.build_memory_snapshot(str(workspace_b))
    assert "NWAFU cluster access rules" in knowledge_snapshot["shared_memory"]


def test_project_context_build_daily_projection_prefers_phase_state_over_user_echo(tmp_path) -> None:
    db_path = tmp_path / "daily.sqlite3"
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
            ("demo", "demo", str(workspace), "", "Project: demo"),
        )

    store = ProjectContextStore(str(db_path))
    store.upsert_project_memory(
        "demo",
        focus="检查 strict rerun 结果并确认 cell type 命名是否一致",
        recent_context="user: 查看链接恢复",
        memory="Task: 检查 strict rerun 结果并确认 cell type 命名是否一致 | State: user: 查看链接恢复",
        recent_hints=["user: 重新跑给我全量提交好"],
    )

    projection = store.build_daily_projection("demo")

    assert projection is not None
    assert projection["current_phase"] == "validation"
    assert projection["state_source"] == "focus"
    assert "查看链接恢复" not in projection["current_state"]
    assert "核查阶段" in projection["current_state"]
    assert projection["next_step"] == ""


def test_project_context_build_daily_projection_exposes_verified_result(tmp_path) -> None:
    db_path = tmp_path / "daily.sqlite3"
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
            ("demo", "demo", str(workspace), "", "Project: demo"),
        )

    store = ProjectContextStore(str(db_path))
    store.upsert_project_memory(
        "demo",
        focus="实现 provider heartbeat fallback",
        recent_context="The runtime health fallback is now fixed and validated.",
        memory="Stable ash execution roadmap for runtime health hardening.",
        recent_hints=["Next: expose heartbeat in dashboard"],
    )

    projection = store.build_daily_projection("demo")
    snapshot = store.build_memory_snapshot(str(workspace))

    assert projection is not None
    assert projection["current_phase"] == "implementation"
    assert "now fixed" in projection["last_verified_result"].lower()
    assert projection["next_step"] == "Next: expose heartbeat in dashboard"
    assert snapshot["current_phase"] == "implementation"
    assert "now fixed" in snapshot["last_verified_result"].lower()


def test_project_context_snapshot_includes_runtime_health(tmp_path) -> None:
    db_path = tmp_path / "runtime-health.sqlite3"
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
            ("demo", "demo", str(workspace), "", "Project: demo"),
        )

    store = ProjectContextStore(str(db_path))
    store.record_runtime_health(
        "demo",
        "codex",
        status="quarantined",
        summary="Entry heartbeat quarantined unhealthy codex session demo-session and fell back to a fresh launch.",
        details={"session_id": "demo-session", "issue": "unhealthy", "quarantined": True},
    )

    snapshot = store.build_memory_snapshot(str(workspace))

    assert snapshot["runtime_health_status"] == "quarantined"
    assert "fell back to a fresh launch" in snapshot["runtime_health_summary"]
    assert snapshot["runtime_health_details"]["session_id"] == "demo-session"


def test_project_context_snapshot_includes_auto_continue_state(tmp_path) -> None:
    db_path = tmp_path / "auto-continue.sqlite3"
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
            ("demo", "demo", str(workspace), "", "Project: demo"),
        )

    store = ProjectContextStore(str(db_path))
    store.record_auto_continue_state(
        "demo",
        "codex",
        status="executed",
        summary="Auto-continue executed one step via codex: expose runtime health in dashboard.",
        details={"task_id": "task-1", "next_step": "expose runtime health in dashboard"},
    )

    snapshot = store.build_memory_snapshot(str(workspace))

    assert snapshot["auto_continue_status"] == "executed"
    assert "executed one step via codex" in snapshot["auto_continue_summary"]
    assert snapshot["auto_continue_details"]["task_id"] == "task-1"


def test_project_skill_markdown_includes_automation_protocol(tmp_path) -> None:
    db_path = tmp_path / "skill.sqlite3"
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
            ("demo", "demo", str(workspace), "", "Project: demo"),
        )

    store = ProjectContextStore(str(db_path))
    markdown = store.render_project_skill_markdown("demo")

    assert "## Automation Protocol" in markdown
    assert "skills/automation-runtime/SKILL.md" in markdown
    assert "/autostep [provider] [--explain]" in markdown
    assert "/automonitor [--apply] [--auto-continue]" in markdown
    assert "project-sessions auto-continue <project>" in markdown


def test_ash_workbench_skill_markdown_includes_workbench_protocol(tmp_path) -> None:
    db_path = tmp_path / "skill.sqlite3"
    workspace = tmp_path / "ash-workbench"
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
            ("ash-workbench", "ash-workbench", str(workspace), "", "Project: ash-workbench"),
        )

    store = ProjectContextStore(str(db_path))
    markdown = store.render_project_skill_markdown("ash-workbench")

    assert "## Workbench Protocol" in markdown
    assert "skills/ash-workbench/SKILL.md" in markdown
    assert "control plane for tmux workbenches" in markdown


def test_ash_workbench_memory_markdown_includes_runtime_section(tmp_path) -> None:
    db_path = tmp_path / "memory.sqlite3"
    workspace = tmp_path / "ash-workbench"
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
            ("ash-workbench", "ash-workbench", str(workspace), "", "Project: ash-workbench"),
        )

    store = ProjectContextStore(str(db_path))
    markdown = store.render_project_memory_markdown("ash-workbench")

    assert "## Workbench Runtime" in markdown
    assert "runtime workbench for tmux, ccb, ssh panes" in markdown
