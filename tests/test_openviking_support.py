from pathlib import Path
import importlib.util

from agent_swarm_hub.openviking_support import (
    DEFAULT_EMBEDDING_DIMENSION,
    DEFAULT_EMBEDDING_INPUT,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_VLM_MODEL,
    build_openviking_config_from_env,
    import_project_tree_to_openviking,
    openviking_server_url,
    read_openviking_config,
    read_openviking_overview,
    resolve_openviking_config_path,
    sync_project_tree_to_openviking,
    write_openviking_config,
)


def test_build_openviking_config_from_env_uses_ark_defaults():
    config = build_openviking_config_from_env(
        {
            "OPENVIKING_ARK_API_KEY": "test-ark-key",
            "OPENVIKING_STORAGE_WORKSPACE": "/tmp/openviking-data",
        }
    )

    assert config["storage"]["workspace"] == "/tmp/openviking-data"
    assert config["embedding"]["dense"]["provider"] == "volcengine"
    assert config["embedding"]["dense"]["model"] == DEFAULT_EMBEDDING_MODEL
    assert config["embedding"]["dense"]["api_key"] == "test-ark-key"
    assert config["embedding"]["dense"]["dimension"] == DEFAULT_EMBEDDING_DIMENSION
    assert config["embedding"]["dense"]["input"] == DEFAULT_EMBEDDING_INPUT
    assert config["vlm"]["provider"] == "volcengine"
    assert config["vlm"]["model"] == DEFAULT_VLM_MODEL
    assert config["vlm"]["api_key"] == "test-ark-key"


def test_build_openviking_config_from_env_supports_split_keys():
    config = build_openviking_config_from_env(
        {
            "OPENVIKING_VLM_API_KEY": "vlm-key",
            "OPENVIKING_EMBEDDING_API_KEY": "embedding-key",
        }
    )

    assert config["vlm"]["api_key"] == "vlm-key"
    assert config["embedding"]["dense"]["api_key"] == "embedding-key"


def test_write_and_read_openviking_config_round_trip(tmp_path):
    config = build_openviking_config_from_env(
        {
            "OPENVIKING_ARK_API_KEY": "test-ark-key",
            "OPENVIKING_STORAGE_WORKSPACE": str(tmp_path / "data"),
        }
    )

    output = write_openviking_config(config, tmp_path / "ov.conf")

    assert read_openviking_config(output) == config


def test_resolve_openviking_config_path_prefers_explicit_and_env(tmp_path, monkeypatch):
    explicit = tmp_path / "explicit.json"
    explicit.write_text("{}", encoding="utf-8")
    env_path = tmp_path / "env.json"
    env_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("OPENVIKING_CONFIG_FILE", str(env_path))

    assert resolve_openviking_config_path(explicit) == explicit.resolve()
    assert resolve_openviking_config_path() == env_path.resolve()


def test_openviking_server_url_uses_server_config():
    assert openviking_server_url({"server": {"host": "127.0.0.2", "port": 1999}}) == "http://127.0.0.2:1999"


def test_read_openviking_overview_uses_sync_http_client(monkeypatch, tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        '{"server": {"host": "127.0.0.1", "port": 1933}}',
        encoding="utf-8",
    )
    called = {}

    class FakeClient:
        def __init__(self, url, timeout):
            called["url"] = url
            called["timeout"] = timeout

        def initialize(self):
            called["initialized"] = True

        def overview(self, uri):
            called["uri"] = uri
            return "project overview"

        def close(self):
            called["closed"] = True

    monkeypatch.setitem(__import__("sys").modules, "openviking_cli.client.sync_http", type("M", (), {"SyncHTTPClient": FakeClient})())

    assert read_openviking_overview("viking://resources/projects/demo", config_path=config_path, timeout=0.2) == "project overview"
    assert called["url"] == "http://127.0.0.1:1933"
    assert called["uri"] == "viking://resources/projects/demo"
    assert called["initialized"] is True
    assert called["closed"] is True


def test_read_openviking_overview_falls_back_to_import_tree(monkeypatch, tmp_path):
    imports_root = tmp_path / "imports" / "projects" / "demo"
    (imports_root / "runtime").mkdir(parents=True, exist_ok=True)
    (imports_root / "README.md").write_text("# demo\n\nProject scoped tree.\n", encoding="utf-8")
    (imports_root / "runtime" / "memory_bundle.md").write_text("Current focus: ship OV-backed memory.\n", encoding="utf-8")
    monkeypatch.setattr("agent_swarm_hub.openviking_support.DEFAULT_IMPORT_TREE_ROOT", imports_root.parent)

    assert "Project scoped tree." in read_openviking_overview("viking://resources/projects/demo")
    assert "Current focus: ship OV-backed memory." in read_openviking_overview("viking://resources/projects/demo")


def test_import_project_tree_to_openviking_pushes_live_tree(monkeypatch, tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        '{"server": {"host": "127.0.0.1", "port": 1933}}',
        encoding="utf-8",
    )
    project_root = tmp_path / "imports" / "projects" / "demo"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "README.md").write_text("# demo\n", encoding="utf-8")
    monkeypatch.setattr("agent_swarm_hub.openviking_support.DEFAULT_IMPORT_TREE_ROOT", project_root.parent)
    monkeypatch.setitem(__import__("sys").modules, "openviking", object())
    calls = []

    class FakeClient:
        def __init__(self, url, timeout):
            calls.append(("init", url, timeout))

        def initialize(self):
            calls.append(("initialize",))

        def mkdir(self, uri):
            calls.append(("mkdir", uri))

        def rm(self, uri, recursive=False):
            calls.append(("rm", uri, recursive))

        def add_resource(self, path, parent=None, wait=False):
            calls.append(("add_resource", path, parent, wait))

        def close(self):
            calls.append(("close",))

    monkeypatch.setitem(__import__("sys").modules, "openviking_cli.client.sync_http", type("M", (), {"SyncHTTPClient": FakeClient})())
    monkeypatch.setenv("NO_PROXY", "old")
    monkeypatch.setenv("no_proxy", "old")

    assert import_project_tree_to_openviking("demo", config_path=config_path) is True
    assert ("mkdir", "viking://resources/projects") in calls
    assert ("rm", "viking://resources/projects/demo", True) in calls
    assert ("add_resource", str(project_root), "viking://resources/projects", True) in calls
    assert ("close",) in calls
    assert __import__("os").environ["NO_PROXY"] == "old"
    assert __import__("os").environ["no_proxy"] == "old"


def test_sync_project_tree_to_openviking_updates_files_in_place(monkeypatch, tmp_path):
    config_path = tmp_path / "ov.conf"
    config_path.write_text(
        '{"server": {"host": "127.0.0.1", "port": 1933}}',
        encoding="utf-8",
    )
    project_root = tmp_path / "imports" / "projects" / "demo"
    (project_root / "memory").mkdir(parents=True, exist_ok=True)
    (project_root / "runtime").mkdir(parents=True, exist_ok=True)
    (project_root / "README.md").write_text("# demo\n", encoding="utf-8")
    (project_root / "memory" / "PROJECT_MEMORY.md").write_text("focus\n", encoding="utf-8")
    (project_root / "runtime" / "memory_bundle.md").write_text("bundle\n", encoding="utf-8")
    monkeypatch.setattr("agent_swarm_hub.openviking_support.DEFAULT_IMPORT_TREE_ROOT", project_root.parent)
    monkeypatch.setitem(__import__("sys").modules, "openviking", object())
    calls = []

    class FakeClient:
        def __init__(self, url, timeout):
            calls.append(("init", url, timeout))

        def initialize(self):
            calls.append(("initialize",))

        def mkdir(self, uri):
            calls.append(("mkdir", uri))

        def rm(self, uri, recursive=False):
            calls.append(("rm", uri, recursive))

        def add_resource(self, path, parent=None, wait=False):
            calls.append(("add_resource", Path(path).name, parent, wait))

        def close(self):
            calls.append(("close",))

    monkeypatch.setitem(__import__("sys").modules, "openviking_cli.client.sync_http", type("M", (), {"SyncHTTPClient": FakeClient})())

    assert sync_project_tree_to_openviking("demo", config_path=config_path) is True
    assert ("mkdir", "viking://resources/projects/demo/memory") in calls
    assert ("rm", "viking://resources/projects/demo/README.md", False) in calls
    assert ("add_resource", "README.md", "viking://resources/projects/demo", True) in calls
    assert ("add_resource", "PROJECT_MEMORY.md", "viking://resources/projects/demo/memory", True) in calls
    assert ("add_resource", "memory_bundle.md", "viking://resources/projects/demo/runtime", True) in calls


def test_memory_bundle_prefers_current_agent_cli_dialogue_and_bound_sessions(monkeypatch, tmp_path):
    script_path = Path("/Users/sunxiangrong/dev/cli/git/agent-swarm-hub/scripts/build-openviking-memory-bundle.py")
    spec = importlib.util.spec_from_file_location("build_openviking_memory_bundle", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    workspace = tmp_path / "demo"
    workspace.mkdir()
    (workspace / "PROJECT_MEMORY.md").write_text(
        "# PROJECT_MEMORY\n\n## Current Focus\nShip OV-backed memory.\n\n## Current State\nOV is now the primary project context.\n",
        encoding="utf-8",
    )
    (workspace / "PROJECT_SKILL.md").write_text(
        "# PROJECT_SKILL\n\n## Work Rules\n- Prefer OV project context first.\n",
        encoding="utf-8",
    )

    class FakeStore:
        def get_project_memory(self, project_id):
            return {"focus": "", "current_state": "", "memory": "Cache summary.", "recent_hints": []}

        def get_current_project_sessions(self, project_id):
            return {"codex": "codex-session-1"}

        def list_project_sessions(self, project_id, provider=None, include_archived=True):
            return [
                {
                    "provider": "codex",
                    "session_id": "codex-session-1",
                    "title": "Resume current project",
                    "summary": "Working on OV-backed memory lifecycle.",
                    "cwd": str(workspace),
                }
            ]

    monkeypatch.setattr(module, "_load_project", lambda project_id: (FakeStore(), type("P", (), {"workspace_path": str(workspace)})()))
    monkeypatch.setattr(module, "_load_runtime_snapshot", lambda project_id: ("demo", "execution", "Runtime summary", "claude-a", "codex-a", "task-1"))
    monkeypatch.setattr(module, "_load_current_agent_cli_dialogue", lambda project_id: [("user", "Use current CLI dialogue first.", "2026-03-22T12:00:00Z")])
    monkeypatch.setattr(module, "_load_recent_messages", lambda project_id: [("assistant", "fallback", "2026-03-22T11:00:00Z")])
    monkeypatch.setattr(module, "_load_bound_session_summaries", lambda project_id: ["codex session codex-session-1 | title=Resume current project | summary=Working on OV-backed memory lifecycle."])
    monkeypatch.setattr(module, "_load_recent_handoffs", lambda project_id: [])

    bundle = module.build_bundle("demo")

    assert "## Key Decisions From Recent Dialogue" in bundle
    assert "Use current CLI dialogue first." in bundle
    assert "## Bound Session Snapshots" in bundle
    assert "codex session codex-session-1" in bundle
