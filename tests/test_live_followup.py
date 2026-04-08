from pathlib import Path
import sqlite3

from agent_swarm_hub.live_followup import send_followup_to_live_session
from agent_swarm_hub.project_context import ProjectContextStore


def _seed_project(store: ProjectContextStore, *, project_id: str, workspace_path: Path, provider: str, raw_session_id: str) -> None:
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "INSERT INTO projects (project_id, title, workspace_path, profile, summary) VALUES (?, ?, ?, ?, ?)",
            (project_id, project_id, str(workspace_path), "", ""),
        )
        conn.execute(
            "INSERT INTO provider_bindings (project_id, provider, raw_session_id) VALUES (?, ?, ?)",
            (project_id, provider, raw_session_id),
        )


def test_send_followup_to_live_session_injects_text_into_live_provider(monkeypatch, tmp_path) -> None:
    project_db = tmp_path / "project.sqlite3"
    workspace = tmp_path / "scpagwas_celltype"
    workspace.mkdir()
    store = ProjectContextStore(str(project_db))
    _seed_project(
        store,
        project_id="scpagwas_celltype",
        workspace_path=workspace,
        provider="codex",
        raw_session_id="codex-live-1",
    )

    captured: dict[str, str] = {}

    class FakeBackend:
        def send_text(self, pane_id: str, content: str) -> None:
            captured["pane_id"] = pane_id
            captured["content"] = content

    class FakeSession:
        codex_session_id = "codex-live-1"

        def ensure_pane(self):
            return True, "%12"

        def backend(self):
            return FakeBackend()

    monkeypatch.setattr(
        "agent_swarm_hub.live_followup._load_provider_project_session",
        lambda *, provider, work_dir: FakeSession(),
    )

    result = send_followup_to_live_session(
        "scpagwas_celltype",
        provider="codex",
        prompt="请检查服务器任务是否全部完成；如果完成就继续结果核查。",
        context_store=store,
    )

    assert captured == {
        "pane_id": "%12",
        "content": "请检查服务器任务是否全部完成；如果完成就继续结果核查。",
    }
    assert result.project_id == "scpagwas_celltype"
    assert result.provider == "codex"
    assert result.pane_id == "%12"
    assert result.session_id == "codex-live-1"
    assert result.workspace_path == str(workspace)
