import json
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

from agent_swarm_hub import cli_ops
from agent_swarm_hub.cli import ash_chat_main, ash_swarm_main, main
from agent_swarm_hub.adapter import CCConnectAdapter
from agent_swarm_hub.paths import ccb_lib_dir, project_session_db_path, provider_command
from agent_swarm_hub.session_store import SessionStore


def _write_codex_session(home: Path, session_id: str, cwd: str) -> Path:
    session_dir = home / ".codex" / "sessions" / "2026" / "03" / "17"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_file = session_dir / f"rollout-2026-03-17T10-00-00-{session_id}.jsonl"
    session_file.write_text(
        f'{{"timestamp":"2026-03-17T10:00:00Z","type":"session_meta","payload":{{"id":"{session_id}","cwd":"{cwd}"}}}}\n',
        encoding="utf-8",
    )
    return session_file


def _write_codex_history(home: Path, session_id: str, *texts: str) -> Path:
    history_path = home / ".codex" / "history.jsonl"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as handle:
        for text in texts:
            handle.write(json.dumps({"session_id": session_id, "text": text}, ensure_ascii=False) + "\n")
    return history_path


def _patch_native_run(monkeypatch, captured: dict, *, after_run=None) -> None:
    def fake_run(argv, env=None, cwd=None, check=False, capture_output=False, text=False):
        if argv == ["ps", "-ax", "-o", "pid=,command="]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        call = {
            "command": argv[0],
            "argv": argv,
            "env": env or {},
            "cwd": cwd,
        }
        captured.setdefault("calls", []).append(call)
        if "command" not in captured:
            captured["command"] = call["command"]
            captured["argv"] = call["argv"]
            captured["env"] = call["env"]
            captured["cwd"] = call["cwd"]
        if after_run is not None:
            after_run(argv, env or {}, cwd)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("agent_swarm_hub.cli.subprocess.run", fake_run)


def test_provider_command_prefers_path_binary_when_wrapper_missing(monkeypatch, tmp_path) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("ASH_CODEX_BIN", raising=False)
    monkeypatch.setattr("agent_swarm_hub.paths.shutil.which", lambda name: f"/opt/test/{name}" if name == "codex" else None)

    assert provider_command("codex") == "/opt/test/codex"


def test_provider_command_prefers_explicit_env(monkeypatch) -> None:
    monkeypatch.setenv("ASH_CLAUDE_BIN", "/tmp/custom-claude")

    assert provider_command("claude") == "/tmp/custom-claude"


def test_project_session_db_defaults_to_cli_root() -> None:
    expected = Path("/Users/sunxiangrong/dev/cli/local-skills/project-session-manager/data/sessions.sqlite3")
    assert project_session_db_path() == expected


def test_ccb_lib_dir_defaults_to_cli_root() -> None:
    expected = Path("/Users/sunxiangrong/dev/cli/Codex/claude_code_bridge/lib")
    assert ccb_lib_dir() == expected


def test_shared_projects_as_workspaces_default_to_auto_transport(monkeypatch, tmp_path) -> None:
    project_db = tmp_path / "project-sessions.sqlite3"
    workspace = tmp_path / "demo"
    projects_dir = tmp_path / "projects"
    workspace.mkdir()
    projects_dir.mkdir()
    with sqlite3.connect(project_db) as conn:
        conn.execute(
            """
            CREATE TABLE projects (
                project_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                workspace_path TEXT NOT NULL DEFAULT '',
                profile TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, '', ?)
            """,
            ("demo", "Demo", str(workspace), "Project: demo"),
        )
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(project_db))
    monkeypatch.setenv("ASH_EXECUTOR", "claude")
    monkeypatch.setenv("ASH_EXECUTOR_TRANSPORT", "ccb")
    monkeypatch.setattr("agent_swarm_hub.workspace_ops.projects_root", lambda: projects_dir)

    workspaces = __import__("agent_swarm_hub.workspace_ops", fromlist=["shared_projects_as_workspaces"]).shared_projects_as_workspaces()

    workspace_ids = {workspace.workspace_id for workspace in workspaces}

    assert "demo" in workspace_ids
    assert "ash-workbench" in workspace_ids
    demo = next(workspace for workspace in workspaces if workspace.workspace_id == "demo")
    workbench = next(workspace for workspace in workspaces if workspace.workspace_id == "ash-workbench")
    assert demo.backend == "claude"
    assert demo.transport == "ccb"
    assert workbench.path == str(projects_dir / "ash-workbench")
    assert (projects_dir / "ash-workbench").is_dir()


def test_workspace_record_from_project_defaults_to_auto_transport() -> None:
    workspace = CCConnectAdapter._workspace_record_from_project("demo")
    assert workspace.transport == "auto"


def test_ash_where_script_reports_project_identity(monkeypatch, tmp_path):
    script = Path("/Users/sunxiangrong/dev/cli/git/agent-swarm-hub/scripts/ash-where")
    monkeypatch.setenv("ASH_ACTIVE_WORKSPACE", "project-alpha")
    monkeypatch.setenv("ASH_PROJECT_PATH", str(tmp_path))
    monkeypatch.setenv("ASH_PROJECT_PROVIDER", "codex")
    monkeypatch.setenv("ASH_PROJECT_SESSION_MODE", "resume-project-context")
    monkeypatch.setenv("ASH_PROJECT_SESSION_ID", "codex-session-123")
    monkeypatch.setenv("ASH_PROJECT_MEMORY_FOCUS", "Keep resume stable")

    import subprocess

    result = subprocess.run([str(script), "--json"], check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)

    assert payload["project"] == "project-alpha"
    assert payload["provider"] == "codex"
    assert payload["session_mode"] == "resume-project-context"
    assert payload["session_id"] == "codex-session-123"
    assert payload["focus"] == "Keep resume stable"


def _write_capture_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def test_start_chat_script_routes_to_local_native(tmp_path) -> None:
    script = Path("/Users/sunxiangrong/dev/cli/git/agent-swarm-hub/scripts/start-chat.sh")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "capture.json"
    tmux_capture = tmp_path / "tmux.json"
    python_bin = fake_bin / "python"
    tmux_bin = fake_bin / "tmux"
    _write_capture_executable(
        python_bin,
        f"""#!/usr/bin/env python3
import json, os, sys
with open({str(capture)!r}, "w", encoding="utf-8") as handle:
    json.dump({{"argv": sys.argv[1:], "cwd": os.getcwd(), "pythonpath": os.getenv("PYTHONPATH"), "session_db": os.getenv("ASH_SESSION_DB")}}, handle)
""",
    )
    _write_capture_executable(
        tmux_bin,
        f"""#!/usr/bin/env python3
import json, sys
with open({str(tmux_capture)!r}, "w", encoding="utf-8") as handle:
    json.dump({{"argv": sys.argv[1:]}}, handle)
""",
    )

    env = {
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "CONDA_DEFAULT_ENV": "cli",
        "HOME": str(tmp_path / "home"),
        "TMUX": "test-session",
    }
    result = subprocess.run(["/bin/bash", str(script), "codex", "agent-browser"], check=True, text=True, capture_output=True, env=env)
    payload = json.loads(capture.read_text(encoding="utf-8"))
    tmux_payload = json.loads(tmux_capture.read_text(encoding="utf-8"))

    assert result.returncode == 0
    assert payload["argv"] == ["-m", "agent_swarm_hub.cli", "local-native", "--provider", "codex", "--project", "agent-browser"]
    assert payload["cwd"] == "/Users/sunxiangrong/dev/cli/git/agent-swarm-hub"
    assert payload["pythonpath"] == "src"
    assert payload["session_db"] == "var/db/agent-swarm-hub.sqlite3"
    assert tmux_payload["argv"] == ["select-pane", "-T", "ash-chat | agent-browser | codex"]


def test_start_swarm_script_routes_to_local_chat(tmp_path) -> None:
    script = Path("/Users/sunxiangrong/dev/cli/git/agent-swarm-hub/scripts/start-swarm.sh")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "capture.json"
    tmux_capture = tmp_path / "tmux.json"
    conda_bin = fake_bin / "conda"
    tmux_bin = fake_bin / "tmux"
    _write_capture_executable(
        conda_bin,
        f"""#!/usr/bin/env python3
import json, os, sys
with open({str(capture)!r}, "w", encoding="utf-8") as handle:
    json.dump({{"argv": sys.argv[1:], "cwd": os.getcwd(), "pythonpath": os.getenv("PYTHONPATH"), "session_db": os.getenv("ASH_SESSION_DB")}}, handle)
""",
    )
    _write_capture_executable(
        tmux_bin,
        f"""#!/usr/bin/env python3
import json, sys
with open({str(tmux_capture)!r}, "w", encoding="utf-8") as handle:
    json.dump({{"argv": sys.argv[1:]}}, handle)
""",
    )

    env = {
        "PATH": f"{fake_bin}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "TMUX": "test-session",
    }
    result = subprocess.run(["/bin/bash", str(script), "claude", "agent-swarm-hub"], check=True, text=True, capture_output=True, env=env)
    payload = json.loads(capture.read_text(encoding="utf-8"))
    tmux_payload = json.loads(tmux_capture.read_text(encoding="utf-8"))

    assert result.returncode == 0
    assert payload["argv"] == [
        "run",
        "--live-stream",
        "-n",
        "cli",
        "python",
        "-m",
        "agent_swarm_hub.cli",
        "local-chat",
        "--provider",
        "claude",
        "--project",
        "agent-swarm-hub",
    ]
    assert payload["cwd"] == "/Users/sunxiangrong/dev/cli/git/agent-swarm-hub"
    assert payload["pythonpath"] == "src"
    assert payload["session_db"] == "var/db/agent-swarm-hub.sqlite3"
    assert tmux_payload["argv"] == ["select-pane", "-T", "ash-swarm | agent-swarm-hub | claude"]


def test_cli_prints_lark_ws_config(monkeypatch, capsys) -> None:
    monkeypatch.setenv("ASH_LARK_ENABLED", "true")
    monkeypatch.setenv("ASH_LARK_APP_ID", "cli_app")
    monkeypatch.setenv("ASH_LARK_VERIFY_TOKEN", "verify")
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "lark-ws", "--print-config"])

    exit_code = main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "cli_app" in output
    assert "verify" in output


def test_cli_prints_telegram_poll_config(monkeypatch, capsys) -> None:
    monkeypatch.setenv("ASH_TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("ASH_TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "telegram-poll", "--print-config"])

    exit_code = main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "bot_token_configured" in output
    assert "True" in output


def test_cli_runs_dashboard(monkeypatch) -> None:
    called = {}

    def fake_serve_dashboard(*, host: str, port: int) -> None:
        called["host"] = host
        called["port"] = port

    monkeypatch.setattr("agent_swarm_hub.cli.serve_dashboard", fake_serve_dashboard)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "dashboard", "--host", "0.0.0.0", "--port", "9001"])

    exit_code = main()

    assert exit_code == 0
    assert called == {"host": "0.0.0.0", "port": 9001}


def test_cli_runs_dashboard_and_opens_browser(monkeypatch) -> None:
    called = {}
    opened = {}

    def fake_serve_dashboard(*, host: str, port: int) -> None:
        called["host"] = host
        called["port"] = port

    def fake_open_dashboard_url(*, host: str, port: int) -> None:
        opened["host"] = host
        opened["port"] = port

    monkeypatch.setattr("agent_swarm_hub.cli.serve_dashboard", fake_serve_dashboard)
    monkeypatch.setattr("agent_swarm_hub.cli._open_dashboard_url", fake_open_dashboard_url)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "dashboard", "--host", "127.0.0.1", "--port", "8765", "--open"])

    exit_code = main()

    assert exit_code == 0
    assert called == {"host": "127.0.0.1", "port": 8765}
    assert opened == {"host": "127.0.0.1", "port": 8765}


def test_cli_openviking_write_only_writes_config_from_env(monkeypatch, tmp_path, capsys) -> None:
    written = {}

    def fake_build():
        return {"storage": {"workspace": "/tmp/ov-data"}}

    def fake_validate(config):
        written["validated"] = config

    def fake_write(config, output_path):
        output = Path(output_path)
        written["config"] = config
        written["output"] = output
        return output

    monkeypatch.setattr("agent_swarm_hub.cli.build_openviking_config_from_env", fake_build)
    monkeypatch.setattr("agent_swarm_hub.cli.validate_openviking_config", fake_validate)
    monkeypatch.setattr("agent_swarm_hub.cli.write_openviking_config", fake_write)
    monkeypatch.setattr("agent_swarm_hub.cli.load_env_file", lambda _path: None)
    monkeypatch.setattr("agent_swarm_hub.cli.apply_runtime_env", lambda: None)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "openviking", "--config-out", str(tmp_path / "ov.conf"), "--write-only"])

    exit_code = main()
    output = capsys.readouterr().out.strip()

    assert exit_code == 0
    assert written["config"] == {"storage": {"workspace": "/tmp/ov-data"}}
    assert written["validated"] == {"storage": {"workspace": "/tmp/ov-data"}}
    assert written["output"] == tmp_path / "ov.conf"
    assert output.endswith("ov.conf")


def test_cli_openviking_reuses_existing_config_without_env(monkeypatch, tmp_path, capsys) -> None:
    config_path = tmp_path / "ov.conf"
    config_path.write_text('{"storage":{"workspace":"/tmp/ov-data"}}\n', encoding="utf-8")
    called = {}

    def fail_build():
        raise AssertionError("should not rebuild config")

    def fake_read(path):
        called["read"] = Path(path)
        return {"storage": {"workspace": "/tmp/ov-data"}}

    def fake_validate(config):
        called["validated"] = config

    def fake_run(argv, env=None, check=False):
        called["argv"] = argv
        called["config_file"] = (env or {}).get("OPENVIKING_CONFIG_FILE")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("agent_swarm_hub.cli.build_openviking_config_from_env", fail_build)
    monkeypatch.setattr("agent_swarm_hub.cli.read_openviking_config", fake_read)
    monkeypatch.setattr("agent_swarm_hub.cli.validate_openviking_config", fake_validate)
    monkeypatch.setattr("agent_swarm_hub.cli.subprocess.run", fake_run)
    monkeypatch.setattr("agent_swarm_hub.cli.load_env_file", lambda _path: None)
    monkeypatch.setattr("agent_swarm_hub.cli.apply_runtime_env", lambda: None)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "ov", "--config-out", str(config_path)])

    exit_code = main()
    output = capsys.readouterr().out.strip()

    assert exit_code == 0
    assert called["read"] == config_path
    assert called["validated"] == {"storage": {"workspace": "/tmp/ov-data"}}
    assert called["argv"] == ["openviking-server"]
    assert called["config_file"] == str(config_path)
    assert output.endswith("ov.conf")


def test_cli_openviking_status_checks_health(monkeypatch, tmp_path, capsys) -> None:
    config_path = tmp_path / "ov.conf"
    config_path.write_text('{"server":{"host":"127.0.0.1","port":1933}}\n', encoding="utf-8")

    monkeypatch.setattr("agent_swarm_hub.cli._ensure_openviking_config", lambda config_out=None: config_path)
    monkeypatch.setattr("agent_swarm_hub.cli.read_openviking_config", lambda path: {"server": {"host": "127.0.0.1", "port": 1933}})

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"status":"ok"}'

    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout=0: FakeResponse())
    monkeypatch.setattr("agent_swarm_hub.cli.load_env_file", lambda _path: None)
    monkeypatch.setattr("agent_swarm_hub.cli.apply_runtime_env", lambda: None)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "ov", "status", "--config-out", str(config_path)])

    exit_code = main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Config:" in output
    assert "Server: http://127.0.0.1:1933" in output
    assert "Health: ok" in output


def test_cli_openviking_sync_invokes_sync_script(monkeypatch) -> None:
    called = {}

    def fake_run(argv, env=None, check=False):
        called["argv"] = argv
        called["env"] = env or {}
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("agent_swarm_hub.cli.subprocess.run", fake_run)
    monkeypatch.setattr("agent_swarm_hub.cli.load_env_file", lambda _path: None)
    monkeypatch.setattr("agent_swarm_hub.cli.apply_runtime_env", lambda: None)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "ov", "sync", "knowledge-system", "--push-live"])

    exit_code = main()

    assert exit_code == 0
    assert "--project" in called["argv"]
    assert "knowledge-system" in called["argv"]
    assert "--push-live" in called["argv"]
    assert called["env"]["NO_PROXY"] == "*"
    assert called["env"]["no_proxy"] == "*"


def test_cli_openviking_sync_can_request_rebuild_tree(monkeypatch) -> None:
    called = {}

    def fake_run(argv, env=None, check=False):
        called["argv"] = argv
        called["env"] = env or {}
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("agent_swarm_hub.cli.subprocess.run", fake_run)
    monkeypatch.setattr("agent_swarm_hub.cli.load_env_file", lambda _path: None)
    monkeypatch.setattr("agent_swarm_hub.cli.apply_runtime_env", lambda: None)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "ov", "sync", "knowledge-system", "--push-live", "--rebuild-tree"])

    exit_code = main()

    assert exit_code == 0
    assert "--rebuild-tree" in called["argv"]


def test_cli_openviking_tui_opens_project_uri(monkeypatch) -> None:
    called = {}

    def fake_run(argv, check=False):
        called["argv"] = argv
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("agent_swarm_hub.cli.subprocess.run", fake_run)
    monkeypatch.setattr("agent_swarm_hub.cli.load_env_file", lambda _path: None)
    monkeypatch.setattr("agent_swarm_hub.cli.apply_runtime_env", lambda: None)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "ov", "tui", "knowledge-system"])

    exit_code = main()

    assert exit_code == 0
    assert called["argv"] == ["ov", "tui", "viking://resources/projects/knowledge-system"]


def test_sync_openviking_project_artifacts_pushes_live_project(monkeypatch) -> None:
    called = {"builds": [], "pushes": []}

    def fake_run(argv, cwd=None, check=False):
        called["builds"].append(argv)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("agent_swarm_hub.cli.subprocess.run", fake_run)
    monkeypatch.setattr(
        "agent_swarm_hub.cli._push_openviking_project_live",
        lambda project_id, rebuild_tree=False: called["pushes"].append(project_id) or True,
    )

    from agent_swarm_hub.cli import _sync_openviking_project_artifacts

    _sync_openviking_project_artifacts("knowledge-system")

    assert len(called["builds"]) == 3
    assert any("build-openviking-project-brain.py" in " ".join(argv) for argv in called["builds"])
    assert called["pushes"] == ["knowledge-system"]


def test_sync_openviking_project_artifacts_rebuilds_tree_when_requested(monkeypatch) -> None:
    called = {"builds": [], "pushes": []}

    def fake_run(argv, cwd=None, check=False):
        called["builds"].append(argv)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("agent_swarm_hub.cli.subprocess.run", fake_run)
    monkeypatch.setattr(
        "agent_swarm_hub.cli._push_openviking_project_live",
        lambda project_id, rebuild_tree=False: called["pushes"].append((project_id, rebuild_tree)) or True,
    )

    from agent_swarm_hub.cli import _sync_openviking_project_artifacts

    _sync_openviking_project_artifacts("knowledge-system", rebuild_tree=True)

    assert any("--rebuild" in argv for argv in called["builds"])
    assert called["pushes"] == [("knowledge-system", True)]


def test_cli_local_chat_auto_prepares_openviking_project(monkeypatch, capsys) -> None:
    called: list[str] = []
    inputs = iter(["/quit"])

    monkeypatch.setenv("ASH_EXECUTOR", "echo")
    monkeypatch.setattr("agent_swarm_hub.cli._auto_prepare_openviking_project", lambda project_id: called.append(project_id))
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    monkeypatch.setattr(
        "sys.argv",
        ["agent-swarm-hub", "local-chat", "--provider", "echo", "--project", "knowledge-system"],
    )

    exit_code = main()
    _ = capsys.readouterr()

    assert exit_code == 0
    assert called == ["knowledge-system"]


def test_cli_local_native_auto_prepares_openviking_project(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    workspace_path = tmp_path / "project"
    workspace_path.mkdir()
    called: list[str] = []

    store = SessionStore(db_path)
    store.upsert_workspace(
        workspace_id="knowledge-system",
        title="knowledge-system",
        path=str(workspace_path),
        backend="codex",
        transport="direct",
    )

    captured = {}
    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setattr("agent_swarm_hub.cli.read_openviking_overview", lambda *args, **kwargs: "OV project overview")
    monkeypatch.setattr("agent_swarm_hub.cli._sync_openviking_project_artifacts", lambda project_id: None)
    monkeypatch.setattr("agent_swarm_hub.cli._auto_prepare_openviking_project", lambda project_id: called.append(project_id))
    _patch_native_run(monkeypatch, captured)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex", "--project", "knowledge-system"])

    exit_code = main()

    assert exit_code == 0
    assert called == ["knowledge-system"]


def test_push_openviking_project_live_respects_disabled_auto(monkeypatch) -> None:
    monkeypatch.setenv("ASH_OPENVIKING_AUTO", "0")
    monkeypatch.setattr("agent_swarm_hub.cli._ensure_openviking_service_running", lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not start OV")))
    monkeypatch.setattr("agent_swarm_hub.cli.import_project_tree_to_openviking", lambda project_id: (_ for _ in ()).throw(AssertionError("should not push live")))

    from agent_swarm_hub.cli import _push_openviking_project_live

    assert _push_openviking_project_live("knowledge-system") is False


def test_cli_without_command_prints_main_menu(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub"])

    exit_code = main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "agent-swarm-hub" in output
    assert "chat [provider] [project]" in output
    assert "swarm [provider] [project]" in output
    assert "dash" in output


def test_cli_chat_shortcut_routes_to_local_native(monkeypatch) -> None:
    called = {}

    def fake_run_local_native(*, provider: str, project: str | None) -> int:
        called["provider"] = provider
        called["project"] = project
        return 0

    monkeypatch.setattr("agent_swarm_hub.cli._run_local_native", fake_run_local_native)
    monkeypatch.setattr("agent_swarm_hub.cli.load_env_file", lambda _path: None)
    monkeypatch.setattr("agent_swarm_hub.cli.apply_runtime_env", lambda: None)
    monkeypatch.setattr("agent_swarm_hub.cli.RuntimeConfig.from_env", lambda: SimpleNamespace(executor_mode="claude"))
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "chat", "codex", "agent-browser"])

    exit_code = main()

    assert exit_code == 0
    assert called == {"provider": "codex", "project": "agent-browser"}


def test_cli_swarm_shortcut_routes_to_local_chat(monkeypatch) -> None:
    called = {}

    def fake_run_local_chat(*, provider: str, chat_id: str, user_id: str, project: str | None) -> int:
        called["provider"] = provider
        called["chat_id"] = chat_id
        called["user_id"] = user_id
        called["project"] = project
        return 0

    monkeypatch.setattr("agent_swarm_hub.cli._run_local_chat", fake_run_local_chat)
    monkeypatch.setattr("agent_swarm_hub.cli.load_env_file", lambda _path: None)
    monkeypatch.setattr("agent_swarm_hub.cli.apply_runtime_env", lambda: None)
    monkeypatch.setattr("agent_swarm_hub.cli.RuntimeConfig.from_env", lambda: SimpleNamespace(executor_mode="claude"))
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "swarm", "agent-swarm-hub"])

    exit_code = main()

    assert exit_code == 0
    assert called == {
        "provider": "claude",
        "chat_id": "local-cli",
        "user_id": "local-user",
        "project": "agent-swarm-hub",
    }


def test_cli_dash_shortcut_routes_to_dashboard(monkeypatch) -> None:
    called = {}

    def fake_serve_dashboard(*, host: str, port: int) -> None:
        called["host"] = host
        called["port"] = port

    monkeypatch.setattr("agent_swarm_hub.cli.serve_dashboard", fake_serve_dashboard)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "dash", "--host", "0.0.0.0", "--port", "9001"])

    exit_code = main()

    assert exit_code == 0
    assert called == {"host": "0.0.0.0", "port": 9001}


def test_cli_dash_shortcut_can_open_browser(monkeypatch) -> None:
    called = {}
    opened = {}

    def fake_serve_dashboard(*, host: str, port: int) -> None:
        called["host"] = host
        called["port"] = port

    def fake_open_dashboard_url(*, host: str, port: int) -> None:
        opened["host"] = host
        opened["port"] = port

    monkeypatch.setattr("agent_swarm_hub.cli.serve_dashboard", fake_serve_dashboard)
    monkeypatch.setattr("agent_swarm_hub.cli._open_dashboard_url", fake_open_dashboard_url)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "dash", "--open"])

    exit_code = main()

    assert exit_code == 0
    assert called == {"host": "127.0.0.1", "port": 8765}
    assert opened == {"host": "127.0.0.1", "port": 8765}


def test_ash_chat_entry_routes_to_local_native(monkeypatch) -> None:
    called = {}

    def fake_run_local_native(*, provider: str, project: str | None) -> int:
        called["provider"] = provider
        called["project"] = project
        return 0

    monkeypatch.setattr("agent_swarm_hub.cli._run_local_native", fake_run_local_native)
    monkeypatch.setattr("agent_swarm_hub.cli.load_env_file", lambda _path: None)
    monkeypatch.setattr("agent_swarm_hub.cli.apply_runtime_env", lambda: None)
    monkeypatch.setattr("agent_swarm_hub.cli.RuntimeConfig.from_env", lambda: SimpleNamespace(executor_mode="claude"))
    monkeypatch.setattr("sys.argv", ["ash-chat", "codex", "agent-browser"])

    exit_code = ash_chat_main()

    assert exit_code == 0
    assert called == {"provider": "codex", "project": "agent-browser"}


def test_ash_swarm_entry_routes_to_local_chat(monkeypatch) -> None:
    called = {}

    def fake_run_local_chat(*, provider: str, chat_id: str, user_id: str, project: str | None) -> int:
        called["provider"] = provider
        called["chat_id"] = chat_id
        called["user_id"] = user_id
        called["project"] = project
        return 0

    monkeypatch.setattr("agent_swarm_hub.cli._run_local_chat", fake_run_local_chat)
    monkeypatch.setattr("agent_swarm_hub.cli.load_env_file", lambda _path: None)
    monkeypatch.setattr("agent_swarm_hub.cli.apply_runtime_env", lambda: None)
    monkeypatch.setattr("agent_swarm_hub.cli.RuntimeConfig.from_env", lambda: SimpleNamespace(executor_mode="claude"))
    monkeypatch.setattr("sys.argv", ["ash-swarm", "agent-swarm-hub"])

    exit_code = ash_swarm_main()

    assert exit_code == 0
    assert called == {
        "provider": "claude",
        "chat_id": "local-cli",
        "user_id": "local-user",
        "project": "agent-swarm-hub",
    }


def test_cli_runs_telegram_poll_forever_by_default(monkeypatch) -> None:
    called = {}

    class FakePollingRunner:
        def __init__(self, service):
            called["service"] = service

        def run_forever(self, *, offset=None):
            called["offset"] = offset

    monkeypatch.setenv("ASH_TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("ASH_TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr("agent_swarm_hub.cli.TelegramPollingRunner", FakePollingRunner)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "telegram-poll"])

    exit_code = main()

    assert exit_code == 0
    assert called["offset"] is None


def test_cli_runs_lark_ws_forever(monkeypatch) -> None:
    called = {"started": False}

    class FakeRunner:
        @classmethod
        def create(cls, config):
            return cls()

        def run_forever(self):
            called["started"] = True

    monkeypatch.setenv("ASH_LARK_ENABLED", "true")
    monkeypatch.setenv("ASH_LARK_APP_ID", "cli_app")
    monkeypatch.setenv("ASH_LARK_APP_SECRET", "secret")
    monkeypatch.setenv("ASH_LARK_VERIFY_TOKEN", "verify")
    monkeypatch.setattr("agent_swarm_hub.cli.LarkWebSocketRunner", FakeRunner)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "lark-ws"])

    exit_code = main()

    assert exit_code == 0
    assert called["started"] is True


def test_cli_local_chat_binds_explicit_project(monkeypatch, capsys) -> None:
    inputs = iter(["/quit"])

    monkeypatch.setenv("ASH_EXECUTOR", "echo")
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    monkeypatch.setattr(
        "sys.argv",
        ["agent-swarm-hub", "local-chat", "--provider", "echo", "--project", "agent-swarm-hub"],
    )

    exit_code = main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Current workspace switched to `agent-swarm-hub`." in output
    assert "just send a normal message" in output


def test_cli_local_chat_prompts_for_project_or_temporary(monkeypatch, capsys) -> None:
    inputs = iter(["temporary", "/quit"])

    monkeypatch.setenv("ASH_EXECUTOR", "echo")
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-chat", "--provider", "echo"])

    exit_code = main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Choose a project or temporary chat:" in output
    assert "Temporary chat selected." in output
    assert "Temporary local chat is ready." in output
    assert "Complex tasks will automatically enter planning / coordinated swarm execution when needed." in output


def test_cli_local_native_can_add_project_from_picker(monkeypatch, tmp_path, capsys) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    projects_dir = tmp_path / "projects"
    captured = {}

    inputs = iter(["My New Project", ""])
    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setenv("ASH_PROJECTS_DIR", str(projects_dir))
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    _patch_native_run(monkeypatch, captured)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex"])

    exit_code = main()

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "No workspaces with an enterable path were found." in output
    assert "Added project `my-new-project`" in output
    assert "Press Enter to enter native codex CLI..." in output
    assert captured["env"]["ASH_ACTIVE_WORKSPACE"] == "my-new-project"
    assert captured["env"]["ASH_PROJECT_PATH"] == str(projects_dir / "my-new-project")
    assert (projects_dir / "my-new-project").is_dir()


def test_cli_local_chat_reprompts_for_invalid_project_selection(monkeypatch, capsys) -> None:
    inputs = iter(["project", "1", "/quit"])
    monkeypatch.setenv("ASH_EXECUTOR", "echo")
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(
        "agent_swarm_hub.cli._shared_projects_as_workspaces",
        lambda: [
            SimpleNamespace(
                workspace_id="agent-swarm-hub",
                title="agent-swarm-hub",
                path="/tmp/agent-swarm-hub",
                backend="claude",
                transport="direct",
            )
        ],
    )
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-chat", "--provider", "echo"])

    exit_code = main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Unknown project selection." in output
    assert "Current workspace switched to `agent-swarm-hub`." in output
    assert "Chat naturally." in output
    assert "Complex tasks will automatically enter planning / coordinated swarm execution when needed." in output


def test_cli_local_chat_ctrl_c_finalizes_memory(monkeypatch, capsys) -> None:
    calls: list[str] = []

    def fake_handle(self, message):
        calls.append(message.text)
        return SimpleNamespace(text="ok", task_id=None)

    monkeypatch.setenv("ASH_EXECUTOR", "echo")
    monkeypatch.setattr("agent_swarm_hub.cli.CCConnectAdapter.handle_message", fake_handle)
    monkeypatch.setattr("agent_swarm_hub.cli.CCConnectAdapter._get_bound_workspace", lambda self, _message: "agent-swarm-hub")
    monkeypatch.setattr("builtins.input", lambda _prompt="": (_ for _ in ()).throw(KeyboardInterrupt()))
    monkeypatch.setattr(
        "sys.argv",
        ["agent-swarm-hub", "local-chat", "--provider", "echo", "--project", "agent-swarm-hub"],
    )

    exit_code = main()
    output = capsys.readouterr().out

    assert exit_code == 130
    assert "/use agent-swarm-hub" in calls
    assert "/quit" in calls
    assert "ok" in output


def test_cli_local_chat_runs_memory_checkpoint(monkeypatch, capsys) -> None:
    inputs = iter(["one", "two", "three", "four", "/quit"])
    checkpoint_calls: list[tuple[str, str]] = []
    consolidate_calls: list[str] = []

    def fake_handle(self, message):
        return SimpleNamespace(text=f"handled {message.text}", task_id=None)

    def fake_sync(self, *, session_key: str, workspace_id: str, **_kwargs):
        checkpoint_calls.append((session_key, workspace_id))

    def fake_consolidate(self, project_id: str, **_kwargs):
        consolidate_calls.append(project_id)
        return True

    monkeypatch.setenv("ASH_EXECUTOR", "echo")
    monkeypatch.setenv("ASH_MEMORY_CHECKPOINT_INTERVAL", "4")
    monkeypatch.setattr("agent_swarm_hub.cli.CCConnectAdapter.handle_message", fake_handle)
    monkeypatch.setattr("agent_swarm_hub.cli.CCConnectAdapter._get_bound_workspace", lambda self, _message: "agent-swarm-hub")
    monkeypatch.setattr("agent_swarm_hub.cli.CCConnectAdapter._resolve_shared_project_id", lambda self, workspace_id: workspace_id)
    monkeypatch.setattr("agent_swarm_hub.cli.CCConnectAdapter._memory_key", lambda self, session_key, workspace_id: session_key)
    monkeypatch.setattr("agent_swarm_hub.cli.CCConnectAdapter._sync_project_memory", fake_sync)
    monkeypatch.setattr("agent_swarm_hub.project_context.ProjectContextStore.consolidate_project_memory", fake_consolidate)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    monkeypatch.setattr(
        "sys.argv",
        ["agent-swarm-hub", "local-chat", "--provider", "echo", "--project", "agent-swarm-hub"],
    )

    exit_code = main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert checkpoint_calls
    assert checkpoint_calls[0][1] == "agent-swarm-hub"
    assert "agent-swarm-hub" in consolidate_calls
    assert "memory checkpoint synced for `agent-swarm-hub`" in output


def test_cli_local_chat_runs_time_based_memory_checkpoint(monkeypatch, capsys) -> None:
    inputs = iter(["one", "two", "/quit"])
    checkpoint_calls: list[tuple[str, str]] = []

    def fake_handle(self, message):
        return SimpleNamespace(text=f"handled {message.text}", task_id=None)

    def fake_sync(self, *, session_key: str, workspace_id: str, **_kwargs):
        checkpoint_calls.append((session_key, workspace_id))

    class FakeMonotonic:
        def __init__(self):
            self.values = iter([0.0, 700.0, 701.0])

        def __call__(self):
            return next(self.values)

    monkeypatch.setenv("ASH_EXECUTOR", "echo")
    monkeypatch.setenv("ASH_MEMORY_CHECKPOINT_INTERVAL", "8")
    monkeypatch.setenv("ASH_MEMORY_CHECKPOINT_SECONDS", "600")
    monkeypatch.setattr("agent_swarm_hub.cli.time.monotonic", FakeMonotonic())
    monkeypatch.setattr("agent_swarm_hub.cli.CCConnectAdapter.handle_message", fake_handle)
    monkeypatch.setattr("agent_swarm_hub.cli.CCConnectAdapter._get_bound_workspace", lambda self, _message: "agent-swarm-hub")
    monkeypatch.setattr("agent_swarm_hub.cli.CCConnectAdapter._resolve_shared_project_id", lambda self, workspace_id: workspace_id)
    monkeypatch.setattr("agent_swarm_hub.cli.CCConnectAdapter._memory_key", lambda self, session_key, workspace_id: session_key)
    monkeypatch.setattr("agent_swarm_hub.cli.CCConnectAdapter._sync_project_memory", fake_sync)
    monkeypatch.setattr("agent_swarm_hub.project_context.ProjectContextStore.consolidate_project_memory", lambda *args, **kwargs: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    monkeypatch.setattr(
        "sys.argv",
        ["agent-swarm-hub", "local-chat", "--provider", "echo", "--project", "agent-swarm-hub"],
    )

    exit_code = main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert checkpoint_calls
    assert checkpoint_calls[0][1] == "agent-swarm-hub"
    assert "memory checkpoint synced for `agent-swarm-hub`" in output


def test_cli_local_native_launches_provider_in_workspace(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    workspace_path = tmp_path / "project"
    workspace_path.mkdir()

    from agent_swarm_hub.session_store import SessionStore

    store = SessionStore(db_path)
    store.upsert_workspace(
        workspace_id="project-alpha",
        title="project-alpha",
        path=str(workspace_path),
        backend="codex",
        transport="direct",
    )

    captured = {}

    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setattr("agent_swarm_hub.cli.read_openviking_overview", lambda *args, **kwargs: "OV project overview")
    monkeypatch.setattr("agent_swarm_hub.cli._sync_openviking_project_artifacts", lambda project_id: None)
    _patch_native_run(monkeypatch, captured)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex", "--project", "project-alpha"])

    exit_code = main()

    assert exit_code == 0
    assert captured["command"].endswith("codex")
    assert captured["env"]["ASH_ACTIVE_WORKSPACE"] == "project-alpha"
    assert captured["env"]["CCB_WORK_DIR"] == str(workspace_path)
    assert captured["env"]["CCB_RUN_DIR"] == str(workspace_path)
    assert captured["env"]["PWD"] == str(workspace_path)
    assert captured["env"]["ASH_PROJECT_PROVIDER"] == "codex"
    assert captured["env"]["ASH_PROJECT_SESSION_MODE"] == "fresh-project-context"
    assert "project=project-alpha" in captured["env"]["ASH_PROJECT_IDENTITY_TEXT"]
    assert captured["env"]["ASH_PROJECT_WHERE_COMMAND"] == "ash-where"
    assert any(part.endswith("/scripts") for part in captured["env"]["PATH"].split(":"))
    assert "Project summary for this session:" in captured["argv"][-1]
    assert f"- Project: project-alpha" in captured["argv"][-1]
    assert f"- Path: {workspace_path}" in captured["argv"][-1]
    assert "- OpenViking Overview: OV project overview" in captured["argv"][-1]
    assert "- OpenViking Project Context: viking://resources/projects/project-alpha" in captured["argv"][-1]
    assert f"- Local Memory View: {workspace_path}/PROJECT_MEMORY.md" in captured["argv"][-1]
    assert f"- Local Rules View: {workspace_path}/PROJECT_SKILL.md" in captured["argv"][-1]
    assert "Use the OpenViking project context as the project-scoped source when available; use the local files as exported startup views." in captured["argv"][-1]


def test_cli_local_native_resumes_shared_project_session(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    workspace_path = tmp_path / "project"
    workspace_path.mkdir()
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    fake_home = tmp_path / "home"

    from agent_swarm_hub.session_store import SessionStore

    store = SessionStore(db_path)
    store.upsert_workspace(
        workspace_id="project-alpha",
        title="project-alpha",
        path=str(workspace_path),
        backend="codex",
        transport="direct",
    )

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "project-alpha",
                "project-alpha",
                str(workspace_path),
                "Project alpha profile",
                "Project: project-alpha\nCurrent focus: Keep resume stable\nRecent context: Native CLI entry should reuse the right conversation",
            ),
        )
        conn.execute(
            """
            INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, notes, source_path, cwd, last_used_at)
            VALUES (?, ?, ?, 'active', '', '', '', '2026-03-17T10:00:00+00:00')
            """,
            ("codex", "codex-session-123", "project-alpha"),
        )

    captured = {}

    _write_codex_session(fake_home, "codex-session-123", str(workspace_path))
    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr("agent_swarm_hub.cli.read_openviking_overview", lambda *args, **kwargs: "OV project overview")
    monkeypatch.setattr("agent_swarm_hub.cli._sync_openviking_project_artifacts", lambda project_id: None)
    _patch_native_run(monkeypatch, captured)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex", "--project", "project-alpha"])

    exit_code = main()

    assert exit_code == 0
    assert captured["argv"][1:-1] == ["--no-alt-screen", "-C", str(workspace_path), "resume", "codex-session-123"]
    assert captured["env"]["ASH_PROVIDER_SESSION_ID"] == "codex-session-123"
    assert captured["env"]["ASH_CODEX_SESSION_ID"] == "codex-session-123"
    assert captured["env"]["ASH_PROJECT_PATH"] == str(workspace_path)
    assert captured["env"]["PWD"] == str(workspace_path)
    assert captured["env"]["ASH_PROJECT_MEMORY_PROJECT_ID"] == "project-alpha"
    assert captured["env"]["ASH_PROJECT_MEMORY_PROFILE"] == "Project alpha profile"
    assert captured["env"]["ASH_PROJECT_MEMORY_FOCUS"] == "Keep resume stable"
    assert captured["env"]["ASH_PROJECT_SESSION_MODE"] == "resume-project-context"
    assert captured["env"]["ASH_PROJECT_SESSION_ID"] == "codex-session-123"
    assert "Project summary for this session:" in captured["argv"][-1]
    assert "- Current Focus: Keep resume stable" in captured["argv"][-1]
    assert "- Current State: Native CLI entry should reuse the right conversation" in captured["argv"][-1]
    assert "- Swarm Mode: complex tasks may automatically enter coordinated multi-agent execution" in captured["argv"][-1]
    assert "- Current Trigger: codex" in captured["argv"][-1]
    assert "- Swarm Orchestrator: claude (launched in tmux when coordination starts)" in captured["argv"][-1]
    assert "- Coordination Roles: orchestrator=claude, planner=claude, executor=codex, reviewer=claude" in captured["argv"][-1]
    assert "- Return Target: claude" in captured["argv"][-1]


def test_cli_local_native_skips_codex_resume_when_session_file_is_missing(monkeypatch, tmp_path, capsys) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    workspace_path = tmp_path / "project"
    workspace_path.mkdir()
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    fake_home = tmp_path / "home"
    (fake_home / ".codex" / "sessions").mkdir(parents=True)

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "project-alpha",
                "project-alpha",
                str(workspace_path),
                "Project alpha profile",
                "Project: project-alpha\nCurrent focus: Skip stale resume ids",
            ),
        )
        conn.execute(
            """
            INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, notes, source_path, cwd, last_used_at)
            VALUES (?, ?, ?, 'active', '', '', '', '2026-03-17T10:00:00+00:00')
            """,
            ("codex", "codex-session-missing", "project-alpha"),
        )

    captured = {}

    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setenv("HOME", str(fake_home))
    _patch_native_run(monkeypatch, captured)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex", "--project", "project-alpha"])
    exit_code = main()

    assert exit_code == 0
    output = capsys.readouterr().out
    assert captured["argv"][:4] == [captured["command"], "--no-alt-screen", "-C", str(workspace_path)]
    assert "[agent-swarm-hub] project=project-alpha" in output
    assert "[agent-swarm-hub] focus=Skip stale resume ids" in output
    assert "[agent-swarm-hub] current_codex_session=none" in output
    assert "Project selected: project-alpha" not in output
    assert "ASH_PROVIDER_SESSION_ID" not in captured["env"]


def test_cli_local_native_skips_codex_resume_when_session_workspace_mismatches(monkeypatch, tmp_path, capsys) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    workspace_path = tmp_path / "project"
    workspace_path.mkdir()
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    fake_home = tmp_path / "home"
    session_dir = fake_home / ".codex" / "sessions" / "2026" / "03" / "17"
    session_dir.mkdir(parents=True)
    session_file = session_dir / "rollout-2026-03-17T10-00-00-codex-session-123.jsonl"
    session_file.write_text(
        '{"timestamp":"2026-03-17T10:00:00Z","type":"session_meta","payload":{"id":"codex-session-123","cwd":"/tmp/wrong-workspace"}}\n',
        encoding="utf-8",
    )

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "project-alpha",
                "project-alpha",
                str(workspace_path),
                "Project alpha profile",
                "Project: project-alpha\nCurrent focus: Avoid wrong resumes",
            ),
        )
        conn.execute(
            """
            INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, notes, source_path, cwd, last_used_at)
            VALUES (?, ?, ?, 'active', '', '', '', '2026-03-17T10:00:00+00:00')
            """,
            ("codex", "codex-session-123", "project-alpha"),
        )

    captured = {}

    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setenv("HOME", str(fake_home))
    _patch_native_run(monkeypatch, captured)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex", "--project", "project-alpha"])
    exit_code = main()

    assert exit_code == 0
    output = capsys.readouterr().out
    assert captured["argv"][:4] == [captured["command"], "--no-alt-screen", "-C", str(workspace_path)]
    assert "[agent-swarm-hub] project=project-alpha" in output
    assert "[agent-swarm-hub] focus=Avoid wrong resumes" in output
    assert "[agent-swarm-hub] current_codex_session=none" in output
    assert "Project selected: project-alpha" not in output
    assert "ASH_PROVIDER_SESSION_ID" not in captured["env"]


def test_cli_local_native_resolves_shared_workspace_without_local_row(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    workspace_path = tmp_path / "project"
    workspace_path.mkdir()
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    fake_home = tmp_path / "home"

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "project-alpha",
                "project-alpha",
                str(workspace_path),
                "Project alpha profile",
                "Project: project-alpha\nCurrent focus: Claude handoff\nRecent context: Re-enter the native Claude CLI in the correct path",
            ),
        )
        conn.execute(
            """
            INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, notes, source_path, cwd, last_used_at)
            VALUES (?, ?, ?, 'active', '', '', '', '2026-03-17T10:00:00+00:00')
            """,
            ("claude", "claude-session-abc", "project-alpha"),
        )

    captured = {}

    _write_codex_session(fake_home, "codex-session-123", str(workspace_path))
    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setenv("HOME", str(fake_home))
    _patch_native_run(monkeypatch, captured)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "claude", "--project", "project-alpha"])
    exit_code = main()

    assert exit_code == 0
    assert captured["argv"][1:] == ["--resume", "claude-session-abc"]
    assert captured["env"]["ASH_ACTIVE_WORKSPACE"] == "project-alpha"
    assert captured["env"]["CCB_WORK_DIR"] == str(workspace_path)
    assert captured["cwd"] == str(workspace_path)
    assert captured["env"]["PWD"] == str(workspace_path)
    assert captured["env"]["ASH_CLAUDE_SESSION_ID"] == "claude-session-abc"
    assert captured["env"]["ASH_PROJECT_PATH"] == str(workspace_path)
    assert captured["env"]["ASH_PROJECT_MEMORY_PROJECT_ID"] == "project-alpha"
    assert captured["env"]["ASH_PROJECT_MEMORY_RECENT_CONTEXT"] == "Re-enter the native Claude CLI in the correct path"


def test_cli_local_native_prefers_provider_binding_over_latest_session(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    workspace_path = tmp_path / "project"
    workspace_path.mkdir()
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    fake_home = tmp_path / "home"

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            CREATE TABLE provider_bindings (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, provider)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "project-alpha",
                "project-alpha",
                str(workspace_path),
                "Project alpha profile",
                "Project: project-alpha\nCurrent focus: Prefer explicit bindings",
            ),
        )
        conn.execute(
            """
            INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, notes, source_path, cwd, last_used_at)
            VALUES (?, ?, ?, 'active', '', '', '', '2026-03-17T10:01:00+00:00')
            """,
            ("codex", "codex-session-latest", "project-alpha"),
        )
        conn.execute(
            """
            INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, notes, source_path, cwd, last_used_at)
            VALUES (?, ?, ?, 'archived', '', '', '', '2026-03-17T10:00:00+00:00')
            """,
            ("codex", "codex-session-bound", "project-alpha"),
        )
        conn.execute(
            """
            INSERT INTO provider_bindings (project_id, provider, raw_session_id)
            VALUES (?, ?, ?)
            """,
            ("project-alpha", "codex", "codex-session-bound"),
        )

    captured = {}

    _write_codex_session(fake_home, "codex-session-latest", str(workspace_path))
    _write_codex_session(fake_home, "codex-session-bound", str(workspace_path))
    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setenv("HOME", str(fake_home))
    _patch_native_run(monkeypatch, captured)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex", "--project", "project-alpha"])
    exit_code = main()

    assert exit_code == 0
    assert captured["argv"][1:-1] == ["--no-alt-screen", "-C", str(workspace_path), "resume", "codex-session-bound"]
    assert captured["env"]["ASH_PROVIDER_SESSION_ID"] == "codex-session-bound"


def test_cli_local_native_resumes_bound_codex_session_after_project_path_migration(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    workspace_path = tmp_path / "project"
    workspace_path.mkdir()
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    fake_home = tmp_path / "home"

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            CREATE TABLE provider_bindings (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, provider)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "project-alpha",
                "project-alpha",
                str(workspace_path),
                "Project alpha profile",
                "Project: project-alpha\nCurrent focus: Keep the same project thread after path migration",
            ),
        )
        conn.execute(
            """
            INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, notes, source_path, cwd, last_used_at)
            VALUES (?, ?, ?, 'active', '', '', ?, '2026-03-17T10:00:00+00:00')
            """,
            ("codex", "codex-session-bound", "project-alpha", "/Users/sunxiangrong/Desktop/CLI/Codex"),
        )
        conn.execute(
            """
            INSERT INTO provider_bindings (project_id, provider, raw_session_id)
            VALUES (?, ?, ?)
            """,
            ("project-alpha", "codex", "codex-session-bound"),
        )

    captured = {}

    _write_codex_session(fake_home, "codex-session-bound", "/Users/sunxiangrong/Desktop/CLI/Codex")
    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setenv("HOME", str(fake_home))
    _patch_native_run(monkeypatch, captured)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex", "--project", "project-alpha"])

    exit_code = main()

    assert exit_code == 0
    assert captured["argv"][1:-1] == ["--no-alt-screen", "-C", str(workspace_path), "resume", "codex-session-bound"]
    assert captured["env"]["ASH_PROVIDER_SESSION_ID"] == "codex-session-bound"
    assert "Project summary for this session:" in captured["argv"][-1]


def test_cli_local_native_skips_duplicate_running_codex_resume(monkeypatch, tmp_path, capsys) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    workspace_path = tmp_path / "project"
    workspace_path.mkdir()
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    fake_home = tmp_path / "home"

    from agent_swarm_hub.session_store import SessionStore

    store = SessionStore(db_path)
    store.upsert_workspace(
        workspace_id="project-alpha",
        title="project-alpha",
        path=str(workspace_path),
        backend="codex",
        transport="direct",
    )

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "project-alpha",
                "project-alpha",
                str(workspace_path),
                "Project alpha profile",
                "Project: project-alpha\nCurrent focus: Avoid duplicate resume",
            ),
        )
        conn.execute(
            """
            INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, notes, source_path, cwd, last_used_at)
            VALUES (?, ?, ?, 'active', '', '', ?, '2026-03-17T10:00:00+00:00')
            """,
            ("codex", "codex-session-123", "project-alpha", str(workspace_path)),
        )

    _write_codex_session(fake_home, "codex-session-123", str(workspace_path))
    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr("agent_swarm_hub.cli.read_openviking_overview", lambda *args, **kwargs: "OV project overview")

    real_run = subprocess.run

    def fake_run(argv, env=None, cwd=None, check=False, capture_output=False, text=False):
        if argv == ["ps", "-ax", "-o", "pid=,command="]:
            return SimpleNamespace(
                returncode=0,
                stdout=f"123 /opt/homebrew/Caskroom/codex/0.116.0/codex-aarch64-apple-darwin --no-alt-screen -C {workspace_path} resume codex-session-123\n",
                stderr="",
            )
        return real_run(argv, env=env, cwd=cwd, check=check, capture_output=capture_output, text=text)

    monkeypatch.setattr("agent_swarm_hub.cli.subprocess.run", fake_run)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex", "--project", "project-alpha"])

    exit_code = main()

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "heartbeat check: healthy existing codex process; skip duplicate launch" in output


def test_cli_local_native_reports_entry_heartbeat_before_resume(monkeypatch, tmp_path, capsys) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    workspace_path = tmp_path / "project-alpha"
    workspace_path.mkdir()
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    from agent_swarm_hub.session_store import SessionStore

    store = SessionStore(db_path)
    store.upsert_workspace(
        workspace_id="project-alpha",
        title="project-alpha",
        path=str(workspace_path),
        backend="codex",
        transport="direct",
    )

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            CREATE TABLE provider_bindings (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, provider)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "project-alpha",
                "project-alpha",
                str(workspace_path),
                "Project alpha profile",
                "Project: project-alpha\nCurrent focus: Resume with heartbeat",
            ),
        )
        conn.execute(
            """
            INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, notes, source_path, cwd, last_used_at)
            VALUES (?, ?, ?, 'active', '', '', ?, '2026-03-17T10:00:00+00:00')
            """,
            ("codex", "codex-session-123", "project-alpha", str(workspace_path)),
        )
        conn.execute(
            "INSERT INTO provider_bindings (project_id, provider, raw_session_id) VALUES (?, ?, ?)",
            ("project-alpha", "codex", "codex-session-123"),
        )

    _write_codex_session(fake_home, "codex-session-123", str(workspace_path))
    captured = {}

    def fake_run(argv, env=None, cwd=None, check=False, capture_output=False, text=False):
        if argv == ["ps", "-ax", "-o", "pid=,command="]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        call = {
            "command": argv[0],
            "argv": argv,
            "env": env or {},
            "cwd": cwd,
        }
        captured.setdefault("calls", []).append(call)
        if "command" not in captured:
            captured["command"] = call["command"]
            captured["argv"] = call["argv"]
            captured["env"] = call["env"]
            captured["cwd"] = call["cwd"]
        return SimpleNamespace(returncode=0)

    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr("agent_swarm_hub.cli.read_openviking_overview", lambda *args, **kwargs: "OV project overview")
    monkeypatch.setattr("agent_swarm_hub.cli.subprocess.run", fake_run)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex", "--project", "project-alpha"])

    exit_code = main()

    assert exit_code == 0
    assert "resume" in captured["argv"]
    output = capsys.readouterr().out
    assert "heartbeat check: no running codex process detected" in output


def test_cli_project_summary_prompt_compacts_openviking_overview(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    workspace_path = tmp_path / "project"
    workspace_path.mkdir()
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    fake_home = tmp_path / "home"

    from agent_swarm_hub.session_store import SessionStore

    store = SessionStore(db_path)
    store.upsert_workspace(
        workspace_id="project-alpha",
        title="project-alpha",
        path=str(workspace_path),
        backend="codex",
        transport="direct",
    )

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "project-alpha",
                "project-alpha",
                str(workspace_path),
                "Project alpha profile",
                "Project: project-alpha\nCurrent focus: Compact OV overview",
            ),
        )
        conn.execute(
            """
            INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, notes, source_path, cwd, last_used_at)
            VALUES (?, ?, ?, 'active', '', '', ?, '2026-03-17T10:00:00+00:00')
            """,
            ("codex", "codex-session-123", "project-alpha", str(workspace_path)),
        )

    captured = {}

    _write_codex_session(fake_home, "codex-session-123", str(workspace_path))
    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr("agent_swarm_hub.cli.read_openviking_overview", lambda *args, **kwargs: "Long OV " * 80)
    _patch_native_run(monkeypatch, captured)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex", "--project", "project-alpha"])

    exit_code = main()

    assert exit_code == 0
    prompt = captured["argv"][-1]
    overview_line = next(line for line in prompt.splitlines() if line.startswith("- OpenViking Overview: "))
    assert len(overview_line) < 280
    assert overview_line.endswith("...")


def test_cli_local_native_exports_both_project_provider_sessions(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    workspace_path = tmp_path / "project"
    workspace_path.mkdir()
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    fake_home = tmp_path / "home"

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "project-alpha",
                "project-alpha",
                str(workspace_path),
                "Project alpha profile",
                "Project: project-alpha\nCurrent focus: Coordinate Claude and Codex\nRecent context: Keep both provider bindings visible",
            ),
        )
        conn.execute(
            """
            INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, notes, source_path, cwd, last_used_at)
            VALUES (?, ?, ?, 'active', '', '', '', '2026-03-17T10:00:00+00:00')
            """,
            ("claude", "claude-session-abc", "project-alpha"),
        )
        conn.execute(
            """
            INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, notes, source_path, cwd, last_used_at)
            VALUES (?, ?, ?, 'active', '', '', '', '2026-03-17T10:01:00+00:00')
            """,
            ("codex", "codex-session-123", "project-alpha"),
        )

    captured = {}

    _write_codex_session(fake_home, "codex-session-123", str(workspace_path))
    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr("agent_swarm_hub.cli.read_openviking_overview", lambda *args, **kwargs: "OV project overview")
    _patch_native_run(monkeypatch, captured)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "claude", "--project", "project-alpha"])
    exit_code = main()

    assert exit_code == 0
    assert captured["argv"][1:] == ["--resume", "claude-session-abc"]
    assert captured["env"]["ASH_PROVIDER_SESSION_ID"] == "claude-session-abc"
    assert captured["env"]["ASH_CLAUDE_SESSION_ID"] == "claude-session-abc"
    assert captured["env"]["ASH_CODEX_SESSION_ID"] == "codex-session-123"
    assert captured["env"]["ASH_PROJECT_MEMORY_HINTS"] == ""
    assert captured["env"]["ASH_PROJECT_MEMORY_OVERVIEW"] == "OV project overview"


def test_cli_local_native_rejects_explicit_shared_project_without_path(monkeypatch, tmp_path, capsys) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    shared_db_path = tmp_path / "shared-projects.sqlite3"

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            """
        )
        conn.execute(
            "INSERT INTO projects (project_id, title, workspace_path, profile, summary) VALUES (?, ?, ?, '', '')",
            ("bad-project", "bad-project", ""),
        )

    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "claude", "--project", "bad-project"])

    exit_code = main()
    err = capsys.readouterr().err

    assert exit_code == 2
    assert "Workspace `bad-project` has no enterable path." in err


def test_cli_local_native_picker_hides_projects_without_path(monkeypatch, tmp_path, capsys) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    good_workspace_path = tmp_path / "good-project"
    good_workspace_path.mkdir()

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            """
        )
        conn.execute("INSERT INTO projects (project_id, title, workspace_path) VALUES (?, ?, ?)", ("bad-project", "bad-project", ""))
        conn.execute("INSERT INTO projects (project_id, title, workspace_path) VALUES (?, ?, ?)", ("good-project", "good-project", str(good_workspace_path)))

    captured = {}

    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    inputs = iter(["1", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    _patch_native_run(monkeypatch, captured)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex"])
    exit_code = main()

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "1. good-project" in output
    assert "bad-project" not in output
    assert "Press Enter to enter native codex CLI..." in output
    assert captured["env"]["ASH_ACTIVE_WORKSPACE"] == "good-project"


def test_cli_local_native_picker_hides_projects_with_missing_directory(monkeypatch, tmp_path, capsys) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    good_workspace_path = tmp_path / "good-project"
    good_workspace_path.mkdir()
    missing_workspace_path = tmp_path / "missing-project"

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            """
        )
        conn.execute("INSERT INTO projects (project_id, title, workspace_path) VALUES (?, ?, ?)", ("missing-project", "missing-project", str(missing_workspace_path)))
        conn.execute("INSERT INTO projects (project_id, title, workspace_path) VALUES (?, ?, ?)", ("good-project", "good-project", str(good_workspace_path)))

    captured = {}

    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    inputs = iter(["1", ""])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    _patch_native_run(monkeypatch, captured)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex"])
    exit_code = main()

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "1. good-project" in output
    assert "missing-project" not in output
    assert "Press Enter to enter native codex CLI..." in output
    assert captured["env"]["ASH_ACTIVE_WORKSPACE"] == "good-project"


def test_cli_local_native_fresh_codex_run_rebinds_project_session_and_memory(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    workspace_path = tmp_path / "project"
    workspace_path.mkdir()
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    fake_home = tmp_path / "home"

    from agent_swarm_hub.session_store import SessionStore

    store = SessionStore(db_path)
    store.upsert_workspace(
        workspace_id="project-alpha",
        title="project-alpha",
        path=str(workspace_path),
        backend="codex",
        transport="direct",
    )

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            CREATE TABLE project_memory (
                project_id TEXT PRIMARY KEY,
                focus TEXT NOT NULL DEFAULT '',
                recent_context TEXT NOT NULL DEFAULT '',
                memory TEXT NOT NULL DEFAULT '',
                recent_hints_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE provider_bindings (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, provider)
            );
            CREATE TABLE project_sessions (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                title TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, session_id)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "project-alpha",
                "project-alpha",
                str(workspace_path),
                "Project alpha profile",
                "Project: project-alpha\nCurrent focus: Keep project continuity\nRecent context: Fresh native runs should bind back automatically",
            ),
        )

    captured = {}

    def after_run(_argv, _env, _cwd) -> None:
        _write_codex_session(fake_home, "codex-session-new", str(workspace_path))
        _write_codex_history(fake_home, "codex-session-new", "Need stable project chat", "Remember the current focus")

    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setenv("HOME", str(fake_home))
    _patch_native_run(monkeypatch, captured, after_run=after_run)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex", "--project", "project-alpha"])

    exit_code = main()

    assert exit_code == 0
    assert captured["argv"][:4] == [captured["command"], "--no-alt-screen", "-C", str(workspace_path)]
    with sqlite3.connect(shared_db_path) as conn:
        binding = conn.execute(
            "SELECT raw_session_id FROM provider_bindings WHERE project_id = ? AND provider = ?",
            ("project-alpha", "codex"),
        ).fetchone()
        memory = conn.execute(
            "SELECT focus, recent_context, memory FROM project_memory WHERE project_id = ?",
            ("project-alpha",),
        ).fetchone()
        session = conn.execute(
            "SELECT raw_session_id, cwd FROM provider_sessions WHERE project_id = ? AND provider = ?",
            ("project-alpha", "codex"),
        ).fetchone()
        project_session = conn.execute(
            "SELECT session_id, status, title, cwd FROM project_sessions WHERE project_id = ? AND provider = ?",
            ("project-alpha", "codex"),
        ).fetchone()
        summary = conn.execute(
            "SELECT summary FROM projects WHERE project_id = ?",
            ("project-alpha",),
        ).fetchone()[0]

    assert binding == ("codex-session-new",)
    assert session == ("codex-session-new", str(workspace_path))
    assert project_session == ("codex-session-new", "active", "Remember the current focus", str(workspace_path))
    assert memory == (
        "Need stable project chat",
        "user: Remember the current focus",
        "Task: Need stable project chat | State: user: Remember the current focus",
    )
    assert "Current sessions: codex=codex-session-new" in summary


def test_cli_local_native_fresh_codex_run_syncs_workspace_runtime(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    workspace_path = tmp_path / "project"
    workspace_path.mkdir()
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    fake_home = tmp_path / "home"

    from agent_swarm_hub.session_store import SessionStore

    store = SessionStore(db_path)
    store.upsert_workspace(
        workspace_id="project-alpha",
        title="project-alpha",
        path=str(workspace_path),
        backend="codex",
        transport="direct",
    )

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            CREATE TABLE project_memory (
                project_id TEXT PRIMARY KEY,
                focus TEXT NOT NULL DEFAULT '',
                recent_context TEXT NOT NULL DEFAULT '',
                memory TEXT NOT NULL DEFAULT '',
                recent_hints_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE provider_bindings (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, provider)
            );
            CREATE TABLE project_sessions (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                title TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, session_id)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "project-alpha",
                "project-alpha",
                str(workspace_path),
                "Project alpha profile",
                "Project: project-alpha\nCurrent focus: Keep project continuity\nRecent context: Fresh native runs should bind back automatically",
            ),
        )

    captured = {}

    def after_run(_argv, _env, _cwd) -> None:
        _write_codex_session(fake_home, "codex-session-new", str(workspace_path))
        _write_codex_history(fake_home, "codex-session-new", "Need stable project chat", "Remember the current focus")

    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setenv("HOME", str(fake_home))
    _patch_native_run(monkeypatch, captured, after_run=after_run)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex", "--project", "project-alpha"])

    exit_code = main()

    assert exit_code == 0
    runtime = store.get_workspace_session("local-native:project-alpha:root", "project-alpha")
    assert runtime is not None
    assert runtime.executor_session_id == "codex-session-new"
    assert runtime.codex_session_id == "codex-session-new"
    assert runtime.claude_session_id is None
    assert runtime.phase == "discussion"
    assert runtime.active_task_id
    assert runtime.swarm_state_json == ""
    assert runtime.conversation_summary == "Task: Need stable project chat\nRecent: user: Remember the current focus"


def test_cli_local_native_fresh_codex_run_archives_previous_project_session(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    workspace_path = tmp_path / "project"
    workspace_path.mkdir()
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    fake_home = tmp_path / "home"

    from agent_swarm_hub.session_store import SessionStore

    store = SessionStore(db_path)
    store.upsert_workspace(
        workspace_id="project-alpha",
        title="project-alpha",
        path=str(workspace_path),
        backend="codex",
        transport="direct",
    )

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            CREATE TABLE project_memory (
                project_id TEXT PRIMARY KEY,
                focus TEXT NOT NULL DEFAULT '',
                recent_context TEXT NOT NULL DEFAULT '',
                memory TEXT NOT NULL DEFAULT '',
                recent_hints_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE provider_bindings (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, provider)
            );
            CREATE TABLE project_sessions (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                title TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, session_id)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "project-alpha",
                "project-alpha",
                str(workspace_path),
                "Project alpha profile",
                "Project: project-alpha\nCurrent focus: Keep project continuity\nRecent context: Fresh native runs should bind back automatically",
            ),
        )
        conn.execute(
            """
            INSERT INTO provider_bindings (project_id, provider, raw_session_id)
            VALUES (?, ?, ?)
            """,
            ("project-alpha", "codex", "codex-session-old"),
        )
        conn.execute(
            """
            INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, notes, source_path, cwd, last_used_at)
            VALUES (?, ?, ?, 'active', '', '', ?, '2026-03-17T10:00:00+00:00')
            """,
            ("codex", "codex-session-old", "project-alpha", str(workspace_path)),
        )
        conn.execute(
            """
            INSERT INTO project_sessions (project_id, provider, session_id, status, title, cwd, last_used_at)
            VALUES (?, ?, ?, 'active', ?, ?, '2026-03-17T10:00:00+00:00')
            """,
            ("project-alpha", "codex", "codex-session-old", "Old task", str(workspace_path)),
        )

    captured = {}

    def after_run(_argv, _env, _cwd) -> None:
        _write_codex_session(fake_home, "codex-session-new", str(workspace_path))
        _write_codex_history(fake_home, "codex-session-new", "Need stable project chat")

    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setenv("HOME", str(fake_home))
    _patch_native_run(monkeypatch, captured, after_run=after_run)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex", "--project", "project-alpha"])

    exit_code = main()

    assert exit_code == 0
    with sqlite3.connect(shared_db_path) as conn:
        project_sessions = conn.execute(
            "SELECT session_id, status FROM project_sessions WHERE project_id = ? AND provider = ? ORDER BY session_id",
            ("project-alpha", "codex"),
        ).fetchall()
        provider_sessions = conn.execute(
            "SELECT raw_session_id, status FROM provider_sessions WHERE project_id = ? AND provider = ? ORDER BY raw_session_id",
            ("project-alpha", "codex"),
        ).fetchall()

    assert project_sessions == [("codex-session-new", "active"), ("codex-session-old", "archived")]
    assert provider_sessions == [("codex-session-new", "active"), ("codex-session-old", "archived")]


def test_cli_local_native_meta_memory_questions_do_not_override_project_memory(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    workspace_path = tmp_path / "project"
    workspace_path.mkdir()
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    fake_home = tmp_path / "home"

    from agent_swarm_hub.session_store import SessionStore

    store = SessionStore(db_path)
    store.upsert_workspace(
        workspace_id="project-alpha",
        title="project-alpha",
        path=str(workspace_path),
        backend="codex",
        transport="direct",
    )

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            CREATE TABLE project_memory (
                project_id TEXT PRIMARY KEY,
                focus TEXT NOT NULL DEFAULT '',
                recent_context TEXT NOT NULL DEFAULT '',
                memory TEXT NOT NULL DEFAULT '',
                recent_hints_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE provider_bindings (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, provider)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "project-alpha",
                "project-alpha",
                str(workspace_path),
                "Project alpha profile",
                "Project: project-alpha\nCurrent focus: Keep the real task focus\nRecent context: Continue the project instead of memory meta discussion",
            ),
        )
        conn.execute(
            """
            INSERT INTO project_memory (project_id, focus, recent_context, memory, recent_hints_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "project-alpha",
                "Keep the real task focus",
                "Continue the project instead of memory meta discussion",
                "Existing compact project memory",
                json.dumps(["user: Real project task"], ensure_ascii=False),
            ),
        )

    captured = {}

    def after_run(_argv, _env, _cwd) -> None:
        _write_codex_session(fake_home, "codex-session-new", str(workspace_path))
        _write_codex_history(fake_home, "codex-session-new", "当前是新对话吗 有之前的记忆吗")

    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setenv("HOME", str(fake_home))
    _patch_native_run(monkeypatch, captured, after_run=after_run)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex", "--project", "project-alpha"])

    exit_code = main()

    assert exit_code == 0
    with sqlite3.connect(shared_db_path) as conn:
        memory = conn.execute(
            "SELECT focus, recent_context, memory, recent_hints_json FROM project_memory WHERE project_id = ?",
            ("project-alpha",),
        ).fetchone()

    assert memory == (
        "Keep the real task focus",
        "Continue the project instead of memory meta discussion",
        "Existing compact project memory",
        '["user: Real project task"]',
    )


def test_cli_local_native_extracts_memory_from_meaningful_messages(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    workspace_path = tmp_path / "project-alpha"
    workspace_path.mkdir()
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            CREATE TABLE project_memory (
                project_id TEXT PRIMARY KEY,
                focus TEXT NOT NULL DEFAULT '',
                recent_context TEXT NOT NULL DEFAULT '',
                memory TEXT NOT NULL DEFAULT '',
                recent_hints_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE provider_bindings (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, provider)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "project-alpha",
                "project-alpha",
                str(workspace_path),
                "Project alpha profile",
                "Project: project-alpha\nCurrent focus: Old focus\nRecent context: Old context",
            ),
        )

    captured = {}

    def after_run(_argv, _env, _cwd) -> None:
        _write_codex_session(fake_home, "codex-session-new", str(workspace_path))
        _write_codex_history(
            fake_home,
            "codex-session-new",
            "继续",
            "需要整理 agent-browser 的命令用法并写成一份可复用说明",
            "已经确认 open、wait、snapshot、click 是最小工作流，下一步补成模板",
        )

    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setenv("HOME", str(fake_home))
    _patch_native_run(monkeypatch, captured, after_run=after_run)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex", "--project", "project-alpha"])

    exit_code = main()

    assert exit_code == 0
    with sqlite3.connect(shared_db_path) as conn:
        memory = conn.execute(
            "SELECT focus, recent_context, memory, recent_hints_json FROM project_memory WHERE project_id = ?",
            ("project-alpha",),
        ).fetchone()

    assert memory[0] == "需要整理 agent-browser 的命令用法并写成一份可复用说明"
    assert memory[1] == "user: 已经确认 open、wait、snapshot、click 是最小工作流，下一步补成模板"
    assert "Task: 需要整理 agent-browser 的命令用法并写成一份可复用说明" in memory[2]
    assert "State: user: 已经确认 open、wait、snapshot、click 是最小工作流，下一步补成模板" in memory[2]
    assert memory[3] == json.dumps(
        [
            "user: 需要整理 agent-browser 的命令用法并写成一份可复用说明",
            "user: 已经确认 open、wait、snapshot、click 是最小工作流，下一步补成模板",
        ],
        ensure_ascii=False,
    )


def test_cli_local_native_project_summary_prompt_avoids_repeating_last_hint(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    workspace_path = tmp_path / "project"
    workspace_path.mkdir()
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    fake_home = tmp_path / "home"

    from agent_swarm_hub.session_store import SessionStore

    store = SessionStore(db_path)
    store.upsert_workspace(
        workspace_id="agent-browser",
        title="agent-browser",
        path=str(workspace_path),
        backend="codex",
        transport="direct",
    )

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            CREATE TABLE project_memory (
                project_id TEXT PRIMARY KEY,
                focus TEXT NOT NULL DEFAULT '',
                recent_context TEXT NOT NULL DEFAULT '',
                memory TEXT NOT NULL DEFAULT '',
                recent_hints_json TEXT NOT NULL DEFAULT '[]',
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
                "agent-browser",
                "agent-browser",
                str(workspace_path),
                "Browser automation exploration",
                "Project: agent-browser\nCurrent focus: chrome会做的更好吗\nRecent context: chrome会做的更好吗",
            ),
        )
        conn.execute(
            """
            INSERT INTO project_memory (project_id, focus, recent_context, memory, recent_hints_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "agent-browser",
                "chrome会做的更好吗",
                "chrome会做的更好吗",
                "Compare whether Chrome-native tooling would produce a more reliable browser workflow.",
                json.dumps(["user: chrome会做的更好吗"], ensure_ascii=False),
            ),
        )

    captured = {}

    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setenv("HOME", str(fake_home))
    _patch_native_run(monkeypatch, captured)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex", "--project", "agent-browser"])

    exit_code = main()

    assert exit_code == 0
    assert "- Current Focus: chrome会做的更好吗" in captured["argv"][-1]
    assert "- Current State: chrome会做的更好吗" in captured["argv"][-1]
    assert "- Next Step:" not in captured["argv"][-1]
    assert "- Cache Summary: Compare whether Chrome-native tooling would produce a more reliable browser workflow." in captured["argv"][-1]


def test_cli_local_native_quarantines_unhealthy_existing_codex_session(monkeypatch, tmp_path, capsys) -> None:
    db_path = tmp_path / "sessions.sqlite3"
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    workspace_path = tmp_path / "project-alpha"
    workspace_path.mkdir()
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    from agent_swarm_hub.session_store import SessionStore

    store = SessionStore(db_path)
    store.upsert_workspace(
        workspace_id="project-alpha",
        title="project-alpha",
        path=str(workspace_path),
        backend="codex",
        transport="direct",
    )

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            CREATE TABLE project_memory (
                project_id TEXT PRIMARY KEY,
                focus TEXT NOT NULL DEFAULT '',
                recent_context TEXT NOT NULL DEFAULT '',
                memory TEXT NOT NULL DEFAULT '',
                recent_hints_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE provider_bindings (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, provider)
            );
            CREATE TABLE project_sessions (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                title TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, session_id)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "project-alpha",
                "project-alpha",
                str(workspace_path),
                "Project alpha profile",
                "Project: project-alpha\nCurrent focus: Recover from bad codex session",
            ),
        )
        conn.execute(
            """
            INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, notes, source_path, cwd, last_used_at)
            VALUES (?, ?, ?, 'active', '', '', ?, '2026-03-17T10:00:00+00:00')
            """,
            ("codex", "codex-session-123", "project-alpha", str(workspace_path)),
        )
        conn.execute(
            "INSERT INTO provider_bindings (project_id, provider, raw_session_id) VALUES (?, ?, ?)",
            ("project-alpha", "codex", "codex-session-123"),
        )
        conn.execute(
            """
            INSERT INTO project_sessions (project_id, provider, session_id, status, title, summary, cwd, source_path, last_used_at)
            VALUES (?, ?, ?, 'active', 'Bad session', 'Bad session summary', ?, ?, '2026-03-17T10:00:00+00:00')
            """,
            ("project-alpha", "codex", "codex-session-123", str(workspace_path), str(workspace_path)),
        )

    _write_codex_session(fake_home, "codex-session-123", str(workspace_path))
    captured = {}
    kills: list[tuple[int, int]] = []

    def fake_run(argv, env=None, cwd=None, check=False, capture_output=False, text=False):
        if argv == ["ps", "-ax", "-o", "pid=,command="]:
            return SimpleNamespace(
                returncode=0,
                stdout=f"123 /opt/homebrew/Caskroom/codex/0.116.0/codex-aarch64-apple-darwin --no-alt-screen -C {workspace_path} resume codex-session-123\n",
                stderr="",
            )
        if argv == ["ps", "-p", "123", "-o", "%cpu="]:
            return SimpleNamespace(returncode=0, stdout="99.9\n", stderr="")
        if argv == ["ps", "-p", "123", "-o", "time="]:
            return SimpleNamespace(returncode=0, stdout="28:50\n", stderr="")
        call = {
            "command": argv[0],
            "argv": argv,
            "env": env or {},
            "cwd": cwd,
        }
        captured.setdefault("calls", []).append(call)
        if "command" not in captured:
            captured["command"] = call["command"]
            captured["argv"] = call["argv"]
            captured["env"] = call["env"]
            captured["cwd"] = call["cwd"]
        _write_codex_session(fake_home, "codex-session-new", str(workspace_path))
        _write_codex_history(fake_home, "codex-session-new", "继续项目")
        return SimpleNamespace(returncode=0)

    def fake_kill(pid, sig):
        kills.append((pid, sig))
        if sig == 0:
            raise OSError("process exited")

    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr("agent_swarm_hub.cli.read_openviking_overview", lambda *args, **kwargs: "OV project overview")
    monkeypatch.setattr("agent_swarm_hub.cli.subprocess.run", fake_run)
    monkeypatch.setattr("agent_swarm_hub.runtime_health.os.kill", fake_kill)
    monkeypatch.setattr("agent_swarm_hub.runtime_health.time.sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex", "--project", "project-alpha"])

    exit_code = main()

    assert exit_code == 0
    assert captured["argv"][:4] == [provider_command("codex"), "--no-alt-screen", "-C", str(workspace_path)]
    assert "resume" not in captured["argv"]
    output = capsys.readouterr().out
    assert "heartbeat check: unhealthy existing codex process" in output
    assert kills and kills[0][0] == 123
    with sqlite3.connect(shared_db_path) as conn:
        binding = conn.execute(
            "SELECT raw_session_id FROM provider_bindings WHERE project_id = ? AND provider = ?",
            ("project-alpha", "codex"),
        ).fetchone()
        session_status = conn.execute(
            "SELECT status FROM project_sessions WHERE project_id = ? AND provider = ? AND session_id = ?",
            ("project-alpha", "codex", "codex-session-123"),
        ).fetchone()[0]
        provider_status = conn.execute(
            "SELECT status FROM provider_sessions WHERE project_id = ? AND provider = ? AND raw_session_id = ?",
            ("project-alpha", "codex", "codex-session-123"),
        ).fetchone()[0]
    assert binding == ("codex-session-new",)
    assert session_status == "quarantined"
    assert provider_status == "quarantined"


def test_latest_provider_session_skips_quarantined_binding(tmp_path) -> None:
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    workspace_path = tmp_path / "project-alpha"
    workspace_path.mkdir()
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            CREATE TABLE provider_bindings (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, provider)
            );
            """
        )
        conn.execute(
            "INSERT INTO projects (project_id, title, workspace_path) VALUES (?, ?, ?)",
            ("project-alpha", "project-alpha", str(workspace_path)),
        )
        conn.execute(
            "INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, cwd) VALUES (?, ?, ?, ?, ?)",
            ("codex", "codex-bad", "project-alpha", "quarantined", str(workspace_path)),
        )
        conn.execute(
            "INSERT INTO provider_bindings (project_id, provider, raw_session_id) VALUES (?, ?, ?)",
            ("project-alpha", "codex", "codex-bad"),
        )

    _write_codex_session(fake_home, "codex-bad", str(workspace_path))

    from agent_swarm_hub.native_entry import latest_provider_session
    from agent_swarm_hub.project_context import ProjectContextStore

    store = ProjectContextStore(str(shared_db_path))
    session_id = latest_provider_session(
        project_id="project-alpha",
        provider="codex",
        workspace_path=str(workspace_path),
        context_store=store,
    )

    assert session_id is None
    with sqlite3.connect(shared_db_path) as conn:
        binding = conn.execute(
            "SELECT raw_session_id FROM provider_bindings WHERE project_id = ? AND provider = ?",
            ("project-alpha", "codex"),
        ).fetchone()
    assert binding is None


def test_cli_project_sessions_heartbeat_apply_quarantines_unhealthy_codex_binding(monkeypatch, tmp_path, capsys) -> None:
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    workspace_path = tmp_path / "project-alpha"
    workspace_path.mkdir()
    kills: list[tuple[int, int]] = []

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            CREATE TABLE provider_bindings (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, provider)
            );
            CREATE TABLE project_sessions (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                title TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, session_id)
            );
            """
        )
        conn.execute(
            "INSERT INTO projects (project_id, title, workspace_path) VALUES (?, ?, ?)",
            ("project-alpha", "project-alpha", str(workspace_path)),
        )
        conn.execute(
            "INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, cwd) VALUES (?, ?, ?, ?, ?)",
            ("codex", "codex-bad", "project-alpha", "active", str(workspace_path)),
        )
        conn.execute(
            "INSERT INTO provider_bindings (project_id, provider, raw_session_id) VALUES (?, ?, ?)",
            ("project-alpha", "codex", "codex-bad"),
        )
        conn.execute(
            """
            INSERT INTO project_sessions (project_id, provider, session_id, status, title, summary, cwd, source_path)
            VALUES (?, ?, ?, 'active', 'Bad session', 'Bad session summary', ?, ?)
            """,
            ("project-alpha", "codex", "codex-bad", str(workspace_path), str(workspace_path)),
        )

    def fake_run(argv, env=None, cwd=None, check=False, capture_output=False, text=False):
        if argv == ["ps", "-ax", "-o", "pid=,command="]:
            return SimpleNamespace(
                returncode=0,
                stdout=f"123 /opt/homebrew/Caskroom/codex/0.116.0/codex-aarch64-apple-darwin --no-alt-screen -C {workspace_path} resume codex-bad\n",
                stderr="",
            )
        if argv == ["ps", "-p", "123", "-o", "%cpu="]:
            return SimpleNamespace(returncode=0, stdout="99.9\n", stderr="")
        if argv == ["ps", "-p", "123", "-o", "time="]:
            return SimpleNamespace(returncode=0, stdout="28:50\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_kill(pid, sig):
        kills.append((pid, sig))
        if sig == 0:
            raise OSError("process exited")

    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setattr("agent_swarm_hub.runtime_health.subprocess.run", fake_run)
    monkeypatch.setattr("agent_swarm_hub.runtime_health.os.kill", fake_kill)
    monkeypatch.setattr("agent_swarm_hub.runtime_health.time.sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "project-sessions", "heartbeat", "project-alpha", "--apply"])

    exit_code = main()

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "status=unhealthy" in output
    assert "actions: 1" in output
    assert kills and kills[0][0] == 123
    with sqlite3.connect(shared_db_path) as conn:
        binding = conn.execute(
            "SELECT raw_session_id FROM provider_bindings WHERE project_id = ? AND provider = ?",
            ("project-alpha", "codex"),
        ).fetchone()
        session_status = conn.execute(
            "SELECT status FROM project_sessions WHERE project_id = ? AND provider = ? AND session_id = ?",
            ("project-alpha", "codex", "codex-bad"),
        ).fetchone()[0]
        provider_status = conn.execute(
            "SELECT status FROM provider_sessions WHERE project_id = ? AND provider = ? AND raw_session_id = ?",
            ("project-alpha", "codex", "codex-bad"),
        ).fetchone()[0]
    assert binding is None
    assert session_status == "quarantined"
    assert provider_status == "quarantined"


def test_cli_project_sessions_heartbeat_apply_clears_missing_codex_binding(monkeypatch, tmp_path, capsys) -> None:
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    workspace_path = tmp_path / "project-alpha"
    workspace_path.mkdir()

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            CREATE TABLE provider_bindings (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, provider)
            );
            CREATE TABLE project_sessions (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                title TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, session_id)
            );
            """
        )
        conn.execute(
            "INSERT INTO projects (project_id, title, workspace_path) VALUES (?, ?, ?)",
            ("project-alpha", "project-alpha", str(workspace_path)),
        )
        conn.execute(
            "INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, cwd) VALUES (?, ?, ?, ?, ?)",
            ("codex", "codex-missing", "project-alpha", "active", str(workspace_path)),
        )
        conn.execute(
            "INSERT INTO provider_bindings (project_id, provider, raw_session_id) VALUES (?, ?, ?)",
            ("project-alpha", "codex", "codex-missing"),
        )
        conn.execute(
            """
            INSERT INTO project_sessions (project_id, provider, session_id, status, title, summary, cwd, source_path)
            VALUES (?, ?, ?, 'active', 'Missing session', 'Missing session summary', ?, ?)
            """,
            ("project-alpha", "codex", "codex-missing", str(workspace_path), str(workspace_path)),
        )

    def fake_run(argv, env=None, cwd=None, check=False, capture_output=False, text=False):
        if argv == ["ps", "-ax", "-o", "pid=,command="]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setattr("agent_swarm_hub.runtime_health.subprocess.run", fake_run)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "project-sessions", "heartbeat", "project-alpha", "--apply"])

    exit_code = main()

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "status=missing-binding-process" in output
    assert "actions: 1" in output
    with sqlite3.connect(shared_db_path) as conn:
        binding = conn.execute(
            "SELECT raw_session_id FROM provider_bindings WHERE project_id = ? AND provider = ?",
            ("project-alpha", "codex"),
        ).fetchone()
        session_status = conn.execute(
            "SELECT status FROM project_sessions WHERE project_id = ? AND provider = ? AND session_id = ?",
            ("project-alpha", "codex", "codex-missing"),
        ).fetchone()[0]
    assert binding is None
    assert session_status == "active"


def test_cli_project_sessions_heartbeat_labels_known_unbound_sessions_as_detached(monkeypatch, tmp_path, capsys) -> None:
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    workspace_alpha = tmp_path / "project-alpha"
    workspace_beta = tmp_path / "project-beta"
    workspace_alpha.mkdir()
    workspace_beta.mkdir()

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            CREATE TABLE provider_bindings (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, provider)
            );
            CREATE TABLE project_sessions (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                title TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, session_id)
            );
            """
        )
        conn.execute(
            "INSERT INTO projects (project_id, title, workspace_path) VALUES (?, ?, ?)",
            ("project-alpha", "project-alpha", str(workspace_alpha)),
        )
        conn.execute(
            "INSERT INTO projects (project_id, title, workspace_path) VALUES (?, ?, ?)",
            ("project-beta", "project-beta", str(workspace_beta)),
        )
        conn.execute(
            "INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, cwd) VALUES (?, ?, ?, ?, ?)",
            ("codex", "codex-alpha", "project-alpha", "active", str(workspace_alpha)),
        )
        conn.execute(
            "INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, cwd) VALUES (?, ?, ?, ?, ?)",
            ("codex", "codex-beta", "project-beta", "active", str(workspace_beta)),
        )
        conn.execute(
            "INSERT INTO provider_bindings (project_id, provider, raw_session_id) VALUES (?, ?, ?)",
            ("project-alpha", "codex", "codex-alpha"),
        )
        conn.execute(
            """
            INSERT INTO project_sessions (project_id, provider, session_id, status, title, summary, cwd, source_path)
            VALUES (?, ?, ?, 'active', 'Alpha session', 'Alpha session summary', ?, ?)
            """,
            ("project-alpha", "codex", "codex-alpha", str(workspace_alpha), str(workspace_alpha)),
        )
        conn.execute(
            """
            INSERT INTO project_sessions (project_id, provider, session_id, status, title, summary, cwd, source_path)
            VALUES (?, ?, ?, 'active', 'Beta session', 'Beta session summary', ?, ?)
            """,
            ("project-beta", "codex", "codex-beta", str(workspace_beta), str(workspace_beta)),
        )

    def fake_run(argv, env=None, cwd=None, check=False, capture_output=False, text=False):
        if argv == ["ps", "-ax", "-o", "pid=,command="]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    f"123 /opt/homebrew/Caskroom/codex/0.116.0/codex-aarch64-apple-darwin --no-alt-screen -C {workspace_alpha} resume codex-alpha\n"
                    f"456 /opt/homebrew/Caskroom/codex/0.116.0/codex-aarch64-apple-darwin --no-alt-screen -C {workspace_beta} resume codex-beta\n"
                ),
                stderr="",
            )
        if argv == ["ps", "-p", "123", "-o", "%cpu="]:
            return SimpleNamespace(returncode=0, stdout="2.1\n", stderr="")
        if argv == ["ps", "-p", "123", "-o", "time="]:
            return SimpleNamespace(returncode=0, stdout="00:15\n", stderr="")
        if argv == ["ps", "-p", "456", "-o", "%cpu="]:
            return SimpleNamespace(returncode=0, stdout="1.4\n", stderr="")
        if argv == ["ps", "-p", "456", "-o", "time="]:
            return SimpleNamespace(returncode=0, stdout="00:20\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setattr("agent_swarm_hub.runtime_health.subprocess.run", fake_run)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "project-sessions", "heartbeat", "project-alpha"])

    exit_code = main()

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "project=project-beta provider=codex status=detached-running" not in output
    assert "No provider heartbeat issues detected." in output


def test_cli_project_sessions_heartbeat_all_reports_detached_sessions(monkeypatch, tmp_path, capsys) -> None:
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    workspace_alpha = tmp_path / "project-alpha"
    workspace_beta = tmp_path / "project-beta"
    workspace_alpha.mkdir()
    workspace_beta.mkdir()

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            CREATE TABLE provider_bindings (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, provider)
            );
            CREATE TABLE project_sessions (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                title TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, session_id)
            );
            """
        )
        conn.execute(
            "INSERT INTO projects (project_id, title, workspace_path) VALUES (?, ?, ?)",
            ("project-alpha", "project-alpha", str(workspace_alpha)),
        )
        conn.execute(
            "INSERT INTO projects (project_id, title, workspace_path) VALUES (?, ?, ?)",
            ("project-beta", "project-beta", str(workspace_beta)),
        )
        conn.execute(
            "INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, cwd) VALUES (?, ?, ?, ?, ?)",
            ("codex", "codex-alpha", "project-alpha", "active", str(workspace_alpha)),
        )
        conn.execute(
            "INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, cwd) VALUES (?, ?, ?, ?, ?)",
            ("codex", "codex-beta", "project-beta", "active", str(workspace_beta)),
        )
        conn.execute(
            "INSERT INTO provider_bindings (project_id, provider, raw_session_id) VALUES (?, ?, ?)",
            ("project-alpha", "codex", "codex-alpha"),
        )
        conn.execute(
            """
            INSERT INTO project_sessions (project_id, provider, session_id, status, title, summary, cwd, source_path)
            VALUES (?, ?, ?, 'active', 'Alpha session', 'Alpha session summary', ?, ?)
            """,
            ("project-alpha", "codex", "codex-alpha", str(workspace_alpha), str(workspace_alpha)),
        )
        conn.execute(
            """
            INSERT INTO project_sessions (project_id, provider, session_id, status, title, summary, cwd, source_path)
            VALUES (?, ?, ?, 'active', 'Beta session', 'Beta session summary', ?, ?)
            """,
            ("project-beta", "codex", "codex-beta", str(workspace_beta), str(workspace_beta)),
        )

    def fake_run(argv, env=None, cwd=None, check=False, capture_output=False, text=False):
        if argv == ["ps", "-ax", "-o", "pid=,command="]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    f"123 /opt/homebrew/Caskroom/codex/0.116.0/codex-aarch64-apple-darwin --no-alt-screen -C {workspace_alpha} resume codex-alpha\n"
                    f"456 /opt/homebrew/Caskroom/codex/0.116.0/codex-aarch64-apple-darwin --no-alt-screen -C {workspace_beta} resume codex-beta\n"
                ),
                stderr="",
            )
        if argv == ["ps", "-p", "123", "-o", "%cpu="]:
            return SimpleNamespace(returncode=0, stdout="2.1\n", stderr="")
        if argv == ["ps", "-p", "123", "-o", "time="]:
            return SimpleNamespace(returncode=0, stdout="00:15\n", stderr="")
        if argv == ["ps", "-p", "456", "-o", "%cpu="]:
            return SimpleNamespace(returncode=0, stdout="1.4\n", stderr="")
        if argv == ["ps", "-p", "456", "-o", "time="]:
            return SimpleNamespace(returncode=0, stdout="00:20\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setattr("agent_swarm_hub.runtime_health.subprocess.run", fake_run)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "project-sessions", "heartbeat", "--all"])

    exit_code = main()

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "project=project-beta provider=codex status=detached-running" in output
    assert "No provider heartbeat issues detected." in output


def test_cli_project_sessions_reset_current_clears_binding(monkeypatch, tmp_path, capsys) -> None:
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    workspace_path = tmp_path / "project-alpha"
    workspace_path.mkdir()

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_bindings (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, provider)
            );
            CREATE TABLE project_sessions (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                title TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, session_id)
            );
            """
        )
        conn.execute(
            "INSERT INTO projects (project_id, title, workspace_path) VALUES (?, ?, ?)",
            ("project-alpha", "project-alpha", str(workspace_path)),
        )
        conn.execute(
            "INSERT INTO provider_bindings (project_id, provider, raw_session_id) VALUES (?, ?, ?)",
            ("project-alpha", "codex", "codex-current"),
        )
        conn.execute(
            """
            INSERT INTO project_sessions (project_id, provider, session_id, status, title, summary, cwd, source_path)
            VALUES (?, ?, ?, 'active', 'Current session', 'Current session summary', ?, ?)
            """,
            ("project-alpha", "codex", "codex-current", str(workspace_path), str(workspace_path)),
        )

    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "project-sessions", "reset-current", "project-alpha", "codex"])

    exit_code = main()

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Cleared current codex binding" in output
    with sqlite3.connect(shared_db_path) as conn:
        binding = conn.execute(
            "SELECT raw_session_id FROM provider_bindings WHERE project_id = ? AND provider = ?",
            ("project-alpha", "codex"),
        ).fetchone()
        session_status = conn.execute(
            "SELECT status FROM project_sessions WHERE project_id = ? AND provider = ? AND session_id = ?",
            ("project-alpha", "codex", "codex-current"),
        ).fetchone()[0]
    assert binding is None
    assert session_status == "active"


def test_cli_project_sessions_reset_current_quarantines_binding(monkeypatch, tmp_path, capsys) -> None:
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    workspace_path = tmp_path / "project-alpha"
    workspace_path.mkdir()

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_bindings (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, provider)
            );
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            CREATE TABLE project_sessions (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                title TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, session_id)
            );
            """
        )
        conn.execute(
            "INSERT INTO projects (project_id, title, workspace_path) VALUES (?, ?, ?)",
            ("project-alpha", "project-alpha", str(workspace_path)),
        )
        conn.execute(
            "INSERT INTO provider_bindings (project_id, provider, raw_session_id) VALUES (?, ?, ?)",
            ("project-alpha", "codex", "codex-current"),
        )
        conn.execute(
            "INSERT INTO provider_sessions (provider, raw_session_id, project_id, status, cwd) VALUES (?, ?, ?, ?, ?)",
            ("codex", "codex-current", "project-alpha", "active", str(workspace_path)),
        )
        conn.execute(
            """
            INSERT INTO project_sessions (project_id, provider, session_id, status, title, summary, cwd, source_path)
            VALUES (?, ?, ?, 'active', 'Current session', 'Current session summary', ?, ?)
            """,
            ("project-alpha", "codex", "codex-current", str(workspace_path), str(workspace_path)),
        )

    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "project-sessions", "reset-current", "project-alpha", "codex", "--quarantine"])

    exit_code = main()

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Quarantined current codex session" in output
    with sqlite3.connect(shared_db_path) as conn:
        binding = conn.execute(
            "SELECT raw_session_id FROM provider_bindings WHERE project_id = ? AND provider = ?",
            ("project-alpha", "codex"),
        ).fetchone()
        session_status = conn.execute(
            "SELECT status FROM project_sessions WHERE project_id = ? AND provider = ? AND session_id = ?",
            ("project-alpha", "codex", "codex-current"),
        ).fetchone()[0]
        provider_status = conn.execute(
            "SELECT status FROM provider_sessions WHERE project_id = ? AND provider = ? AND raw_session_id = ?",
            ("project-alpha", "codex", "codex-current"),
        ).fetchone()[0]
    assert binding is None
    assert session_status == "quarantined"
    assert provider_status == "quarantined"


def test_cli_project_sessions_auto_continue_runs_single_step(monkeypatch, tmp_path, capsys) -> None:
    session_db = tmp_path / "sessions.sqlite3"
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    workspace_path = tmp_path / "project-alpha"
    workspace_path.mkdir()

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE project_memory (
                project_id TEXT PRIMARY KEY,
                focus TEXT NOT NULL DEFAULT '',
                recent_context TEXT NOT NULL DEFAULT '',
                memory TEXT NOT NULL DEFAULT '',
                recent_hints_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, '', '')
            """,
            ("project-alpha", "project-alpha", str(workspace_path)),
        )

    from agent_swarm_hub.executor import EchoExecutor
    from agent_swarm_hub.project_context import ProjectContextStore
    from agent_swarm_hub.session_store import SessionStore

    project_store = ProjectContextStore(str(shared_db_path))
    project_store.upsert_project_memory(
        "project-alpha",
        focus="实现最小自动执行器",
        recent_context="runtime health phase 1 已完成，需要开始单步自动推进。",
        memory="Single-step auto-continue should run exactly one meaningful increment and then stop.",
        recent_hints=["Next: expose single-step auto-continue as a project command"],
    )

    class FakeMemoryExecutor:
        def run(self, prompt: str):
            return SimpleNamespace(
                output=json.dumps(
                    {
                        "focus": "实现最小自动执行器",
                        "current_state": "已经接入单步 auto-continue 命令并验证项目能自动推进一小步。",
                        "next_step": "把 auto-continue 的结果投影到 dashboard 和 summary。",
                        "long_term_memory": "Single-step auto execution is the first rung above runtime health hardening.",
                        "key_points": ["把 auto-continue 的结果投影到 dashboard 和 summary。"],
                    },
                    ensure_ascii=False,
                )
            )

    monkeypatch.setenv("ASH_SESSION_DB", str(session_db))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setattr("agent_swarm_hub.auto_continue.build_executor_for_config", lambda **_: EchoExecutor())
    monkeypatch.setattr("agent_swarm_hub.project_context.build_executor_for_config", lambda **_: FakeMemoryExecutor())
    monkeypatch.setattr("agent_swarm_hub.cli._sync_openviking_project_artifacts", lambda _project_id: None)
    monkeypatch.setattr(
        "sys.argv",
        ["agent-swarm-hub", "project-sessions", "auto-continue", "project-alpha", "--provider", "codex"],
    )

    exit_code = main()

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Auto-continue project: project-alpha" in output
    assert "Provider: codex" in output
    assert "Next step: Next: expose single-step auto-continue as a project command" in output

    runtime_store = SessionStore(session_db)
    workspace_session = runtime_store.get_workspace_session("local:auto-runtime:project-alpha:root", "project-alpha")
    assert workspace_session is not None
    assert workspace_session.active_task_id is not None
    assert workspace_session.phase == "discussion"


def test_cli_project_sessions_auto_continue_refuses_blocked_runtime_health(monkeypatch, tmp_path, capsys) -> None:
    session_db = tmp_path / "sessions.sqlite3"
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    workspace_path = tmp_path / "project-alpha"
    workspace_path.mkdir()

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE project_memory (
                project_id TEXT PRIMARY KEY,
                focus TEXT NOT NULL DEFAULT '',
                recent_context TEXT NOT NULL DEFAULT '',
                memory TEXT NOT NULL DEFAULT '',
                recent_hints_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, '', '')
            """,
            ("project-alpha", "project-alpha", str(workspace_path)),
        )

    from agent_swarm_hub.project_context import ProjectContextStore
    from agent_swarm_hub.session_store import SessionStore

    project_store = ProjectContextStore(str(shared_db_path))
    project_store.upsert_project_memory(
        "project-alpha",
        focus="实现最小自动执行器",
        recent_context="runtime health 当前不稳定。",
        memory="Single-step auto execution should pause when runtime health is blocked.",
        recent_hints=["Next: expose single-step auto-continue as a project command"],
    )
    project_store.record_runtime_health(
        "project-alpha",
        "codex",
        status="quarantined",
        summary="Bound codex session is quarantined and must not be auto-continued.",
        details={"session_id": "codex-bad", "issue": "unhealthy"},
    )

    monkeypatch.setenv("ASH_SESSION_DB", str(session_db))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setattr(
        "sys.argv",
        ["agent-swarm-hub", "project-sessions", "auto-continue", "project-alpha", "--provider", "codex"],
    )

    exit_code = main()

    assert exit_code == 1
    output = capsys.readouterr().out
    assert "Auto-continue blocked by runtime health for `project-alpha`: quarantined" in output
    assert "must not be auto-continued" in output

    runtime_store = SessionStore(session_db)
    workspace_session = runtime_store.get_workspace_session("local:auto-runtime:project-alpha:root", "project-alpha")
    assert workspace_session is None


def test_cli_project_sessions_auto_continue_explain_only(monkeypatch, tmp_path, capsys) -> None:
    session_db = tmp_path / "sessions.sqlite3"
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    workspace_path = tmp_path / "project-alpha"
    workspace_path.mkdir()

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE project_memory (
                project_id TEXT PRIMARY KEY,
                focus TEXT NOT NULL DEFAULT '',
                recent_context TEXT NOT NULL DEFAULT '',
                memory TEXT NOT NULL DEFAULT '',
                recent_hints_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, '', '')
            """,
            ("project-alpha", "project-alpha", str(workspace_path)),
        )

    from agent_swarm_hub.project_context import ProjectContextStore
    from agent_swarm_hub.session_store import SessionStore

    project_store = ProjectContextStore(str(shared_db_path))
    project_store.upsert_project_memory(
        "project-alpha",
        focus="实现 explain-only auto-continue",
        recent_context="runtime health 已稳定，可以先解释自动推进计划。",
        memory="Single-step auto execution should support explain-only mode.",
        recent_hints=["Next: expose auto-continue explain mode"],
    )

    monkeypatch.setenv("ASH_SESSION_DB", str(session_db))
    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setattr(
        "sys.argv",
        ["agent-swarm-hub", "project-sessions", "auto-continue", "project-alpha", "--provider", "codex", "--explain"],
    )

    exit_code = main()

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Requested provider: codex" in output
    assert "Auto-step project: project-alpha" in output
    assert "Explain only: no execution performed." in output
    assert "Next step: Next: expose auto-continue explain mode" in output

    runtime_store = SessionStore(session_db)
    workspace_session = runtime_store.get_workspace_session("local:auto-runtime:project-alpha:root", "project-alpha")
    assert workspace_session is None


def test_cli_project_sessions_monitor_routes_arguments(monkeypatch, capsys) -> None:
    captured: dict[str, object] = {}

    def fake_monitor(
        project_id: str | None,
        *,
        monitor_all: bool,
        apply: bool,
        auto_continue_enabled: bool,
        until_complete: bool,
        interval_seconds: float,
        cycles: int,
        sync_project_memory_artifacts_cb,
    ) -> int:
        captured.update(
            {
                "project_id": project_id,
                "monitor_all": monitor_all,
                "apply": apply,
                "auto_continue_enabled": auto_continue_enabled,
                "until_complete": until_complete,
                "interval_seconds": interval_seconds,
                "cycles": cycles,
                "has_sync_cb": callable(sync_project_memory_artifacts_cb),
            }
        )
        print("monitor-routed")
        return 0

    monkeypatch.setattr("agent_swarm_hub.cli_ops.project_sessions_monitor", fake_monitor)
    monkeypatch.setattr(
        "sys.argv",
        [
            "agent-swarm-hub",
            "project-sessions",
            "monitor",
            "project-alpha",
            "--apply",
            "--auto-continue",
            "--interval",
            "5",
            "--cycles",
            "3",
        ],
    )

    exit_code = main()

    assert exit_code == 0
    assert "monitor-routed" in capsys.readouterr().out
    assert captured == {
        "project_id": "project-alpha",
        "monitor_all": False,
        "apply": True,
        "auto_continue_enabled": True,
        "until_complete": False,
        "interval_seconds": 5.0,
        "cycles": 3,
        "has_sync_cb": True,
    }


def test_cli_project_sessions_followup_live_routes_arguments(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_followup(project_id: str, *, provider: str | None, prompt: str) -> int:
        captured.update(
            {
                "project_id": project_id,
                "provider": provider,
                "prompt": prompt,
            }
        )
        return 0

    monkeypatch.setattr("agent_swarm_hub.cli_ops.project_sessions_followup_live", fake_followup)
    monkeypatch.setattr(
        "sys.argv",
        [
            "agent-swarm-hub",
            "project-sessions",
            "followup-live",
            "scpagwas_celltype",
            "--provider",
            "codex",
            "请检查服务器任务是否全部完成",
            "如果完成就继续下一步",
        ],
    )

    exit_code = main()

    assert exit_code == 0
    assert captured == {
        "project_id": "scpagwas_celltype",
        "provider": "codex",
        "prompt": "请检查服务器任务是否全部完成 如果完成就继续下一步",
    }


def test_cli_project_sessions_bridge_policy_routes_arguments(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_bridge_policy(project_id: str, *, init: bool, force: bool, set_ssh_targets: list[str] | None) -> int:
        captured.update(
            {
                "project_id": project_id,
                "init": init,
                "force": force,
                "set_ssh_targets": set_ssh_targets,
            }
        )
        return 0

    monkeypatch.setattr("agent_swarm_hub.cli_ops.project_sessions_bridge_policy", fake_bridge_policy)
    monkeypatch.setattr(
        "sys.argv",
        [
            "agent-swarm-hub",
            "project-sessions",
            "bridge-policy",
            "agent-swarm-hub",
            "--init",
            "--force",
        ],
    )

    exit_code = main()

    assert exit_code == 0
    assert captured == {
        "project_id": "agent-swarm-hub",
        "init": True,
        "force": True,
        "set_ssh_targets": None,
    }


def test_cli_project_sessions_bridge_policy_routes_set_ssh_targets(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_bridge_policy(project_id: str, *, init: bool, force: bool, set_ssh_targets: list[str] | None) -> int:
        captured.update(
            {
                "project_id": project_id,
                "init": init,
                "force": force,
                "set_ssh_targets": set_ssh_targets,
            }
        )
        return 0

    monkeypatch.setattr("agent_swarm_hub.cli_ops.project_sessions_bridge_policy", fake_bridge_policy)
    monkeypatch.setattr(
        "sys.argv",
        [
            "agent-swarm-hub",
            "project-sessions",
            "bridge-policy",
            "agent-swarm-hub",
            "--set-ssh-target",
            "xinong",
            "--set-ssh-target",
            "gpu",
        ],
    )

    exit_code = main()

    assert exit_code == 0
    assert captured == {
        "project_id": "agent-swarm-hub",
        "init": False,
        "force": False,
        "set_ssh_targets": ["xinong", "gpu"],
    }


def test_cli_project_sessions_bridge_env_routes_arguments(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_bridge_env(project_id: str, *, init: bool) -> int:
        captured.update(
            {
                "project_id": project_id,
                "init": init,
            }
        )
        return 0

    monkeypatch.setattr("agent_swarm_hub.cli_ops.project_sessions_bridge_env", fake_bridge_env)
    monkeypatch.setattr(
        "sys.argv",
        [
            "agent-swarm-hub",
            "project-sessions",
            "bridge-env",
            "agent-swarm-hub",
            "--init",
        ],
    )

    exit_code = main()

    assert exit_code == 0
    assert captured == {
        "project_id": "agent-swarm-hub",
        "init": True,
    }


def test_cli_project_sessions_open_tmux_routes_arguments(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_open_tmux(
        project_id: str,
        *,
        provider: str | None,
        bridge_layout: bool,
        ssh_targets: list[str] | None,
        manual_pane: bool,
        secondary_agents: list[str] | None,
    ) -> int:
        captured.update(
            {
                "project_id": project_id,
                "provider": provider,
                "bridge_layout": bridge_layout,
                "ssh_targets": ssh_targets,
                "manual_pane": manual_pane,
                "secondary_agents": secondary_agents,
            }
        )
        return 0

    monkeypatch.setattr("agent_swarm_hub.cli_ops.project_sessions_open_tmux_terminal", fake_open_tmux)
    monkeypatch.setattr(
        "sys.argv",
        [
            "agent-swarm-hub",
            "project-sessions",
            "open-tmux",
            "agent-swarm-hub",
            "--provider",
            "codex",
        ],
    )

    exit_code = main()

    assert exit_code == 0
    assert captured == {
        "project_id": "agent-swarm-hub",
        "provider": "codex",
        "bridge_layout": False,
        "ssh_targets": [],
        "manual_pane": True,
        "secondary_agents": [],
    }


def test_cli_project_sessions_open_tmux_with_bridge_layout_routes_arguments(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_open_tmux(
        project_id: str,
        *,
        provider: str | None,
        bridge_layout: bool,
        ssh_targets: list[str] | None,
        manual_pane: bool,
        secondary_agents: list[str] | None,
    ) -> int:
        captured.update(
            {
                "project_id": project_id,
                "provider": provider,
                "bridge_layout": bridge_layout,
                "ssh_targets": ssh_targets,
                "manual_pane": manual_pane,
                "secondary_agents": secondary_agents,
            }
        )
        return 0

    monkeypatch.setattr("agent_swarm_hub.cli_ops.project_sessions_open_tmux_terminal", fake_open_tmux)
    monkeypatch.setattr(
        "sys.argv",
        [
            "agent-swarm-hub",
            "project-sessions",
            "open-tmux",
            "agent-swarm-hub",
            "--provider",
            "codex",
            "--bridge-layout",
            "--ssh-target",
            "xinong",
            "--ssh-target",
            "ias",
            "--secondary-agent",
            "claude",
            "--no-manual",
        ],
    )

    exit_code = main()

    assert exit_code == 0
    assert captured == {
        "project_id": "agent-swarm-hub",
        "provider": "codex",
        "bridge_layout": True,
        "ssh_targets": ["xinong", "ias"],
        "manual_pane": False,
        "secondary_agents": ["claude"],
    }


def test_cli_project_sessions_bridge_status_routes_arguments(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_bridge_status(
        project_id: str,
        *,
        provider: str | None,
        init: bool,
        exports: bool,
    ) -> int:
        captured.update(
            {
                "project_id": project_id,
                "provider": provider,
                "init": init,
                "exports": exports,
            }
        )
        return 0

    monkeypatch.setattr("agent_swarm_hub.cli_ops.project_sessions_bridge_status", fake_bridge_status)
    monkeypatch.setattr(
        "sys.argv",
        [
            "agent-swarm-hub",
            "project-sessions",
            "bridge-status",
            "agent-swarm-hub",
            "--provider",
            "codex",
            "--init",
            "--exports",
        ],
    )

    exit_code = main()

    assert exit_code == 0
    assert captured == {
        "project_id": "agent-swarm-hub",
        "provider": "codex",
        "init": True,
        "exports": True,
    }


def test_cli_project_sessions_bridge_workbench_routes_arguments(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_bridge_workbench(
        project_id: str,
        *,
        provider: str | None,
        ssh_targets: list[str] | None,
        manual_pane: bool,
        secondary_agents: list[str] | None,
        init: bool,
        exports: bool,
    ) -> int:
        captured.update(
            {
                "project_id": project_id,
                "provider": provider,
                "ssh_targets": ssh_targets,
                "manual_pane": manual_pane,
                "secondary_agents": secondary_agents,
                "init": init,
                "exports": exports,
            }
        )
        return 0

    monkeypatch.setattr("agent_swarm_hub.cli_ops.project_sessions_bridge_workbench", fake_bridge_workbench)
    monkeypatch.setattr(
        "sys.argv",
        [
            "agent-swarm-hub",
            "project-sessions",
            "bridge-workbench",
            "agent-swarm-hub",
            "--provider",
            "codex",
            "--ssh-target",
            "xinong",
            "--ssh-target",
            "ias",
            "--secondary-agent",
            "claude",
            "--init",
            "--exports",
        ],
    )

    exit_code = main()

    assert exit_code == 0
    assert captured == {
        "project_id": "agent-swarm-hub",
        "provider": "codex",
        "ssh_targets": ["xinong", "ias"],
        "manual_pane": True,
        "secondary_agents": ["claude"],
        "init": True,
        "exports": True,
    }


def test_project_sessions_open_tmux_terminal_opens_terminal(monkeypatch, tmp_path, capsys) -> None:
    workspace = tmp_path / "agent-swarm-hub"
    workspace.mkdir()

    class FakeStore:
        def get_project(self, project_id: str):
            if project_id != "agent-swarm-hub":
                return None
            return SimpleNamespace(project_id=project_id, workspace_path=str(workspace))

    launch_calls: list[dict[str, object]] = []
    run_calls: list[list[str]] = []

    def fake_ensure_orchestrator_pane(*, project_id: str, workspace_path: str, provider: str = "claude", launch_mode: str | None = None):
        launch_calls.append(
            {
                "project_id": project_id,
                "workspace_path": workspace_path,
                "provider": provider,
                "launch_mode": launch_mode,
            }
        )
        return {
            "status": "launched",
            "session_name": "ash-codex-agent-swarm-hub",
            "window_index": "4",
            "pane_id": "%7",
        }

    def fake_run(argv, check=False, capture_output=False, text=False):
        run_calls.append(argv)
        if argv[:3] == ["tmux", "has-session", "-t"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli_ops, "ProjectContextStore", lambda: FakeStore())
    monkeypatch.setattr(cli_ops, "ensure_orchestrator_pane", fake_ensure_orchestrator_pane)
    monkeypatch.setattr(cli_ops.subprocess, "run", fake_run)

    exit_code = cli_ops.project_sessions_open_tmux_terminal(
        "agent-swarm-hub",
        provider="codex",
        bridge_layout=False,
        ssh_targets=[],
        manual_pane=True,
        secondary_agents=[],
    )

    assert exit_code == 0
    assert launch_calls == [
        {
            "project_id": "agent-swarm-hub",
            "workspace_path": str(workspace),
            "provider": "codex",
            "launch_mode": "focus",
        }
    ]
    assert run_calls
    assert run_calls[0][:3] == ["tmux", "has-session", "-t"]
    assert any(call[:4] == ["tmux", "set-environment", "-t", "ash-codex-agent-swarm-hub"] for call in run_calls)
    osa_calls = [call for call in run_calls if call[0:2] == ["osascript", "-e"]]
    assert osa_calls
    assert "tmux attach -t ash-codex-agent-swarm-hub" in osa_calls[0][2]
    out = capsys.readouterr().out
    assert "Opened Terminal for `agent-swarm-hub` (codex)" in out
    assert "Applied bridge env to tmux session `ash-codex-agent-swarm-hub`." in out
    assert "project-sessions bridge-env agent-swarm-hub" in out
    assert "project-sessions bridge-status agent-swarm-hub --provider codex" in out


def test_project_sessions_open_tmux_terminal_applies_bridge_layout(monkeypatch, tmp_path) -> None:
    workspace = tmp_path / "agent-swarm-hub"
    workspace.mkdir()

    class FakeStore:
        def get_project(self, project_id: str):
            if project_id != "agent-swarm-hub":
                return None
            return SimpleNamespace(project_id=project_id, workspace_path=str(workspace))

    tmux_calls: list[list[str]] = []

    def fake_ensure_orchestrator_pane(*, project_id: str, workspace_path: str, provider: str = "claude", launch_mode: str | None = None):
        return {
            "status": "launched",
            "session_name": "ash-codex-agent-swarm-hub",
            "window_index": "4",
            "pane_id": "%7",
        }

    def fake_run(argv, check=False, capture_output=False, text=False):
        if argv and argv[0] == "tmux":
            tmux_calls.append(argv)
            if argv[1:3] == ["has-session", "-t"]:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if argv[1:4] == ["list-panes", "-t", "ash-codex-agent-swarm-hub:4"]:
                return SimpleNamespace(returncode=0, stdout="%7\t\tash-chat | agent-swarm-hub | codex\n", stderr="")
            if argv[1:2] == ["split-window"]:
                return SimpleNamespace(returncode=0, stdout="%8\n", stderr="")
            if argv[1:3] == ["set-option", "-p"]:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if argv[1:3] == ["set-environment", "-t"]:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if argv[1:3] == ["select-layout", "-t"]:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli_ops, "ProjectContextStore", lambda: FakeStore())
    monkeypatch.setattr(cli_ops, "ensure_orchestrator_pane", fake_ensure_orchestrator_pane)
    monkeypatch.setattr(cli_ops.subprocess, "run", fake_run)

    exit_code = cli_ops.project_sessions_open_tmux_terminal(
        "agent-swarm-hub",
        provider="codex",
        bridge_layout=True,
        ssh_targets=["xinong"],
        manual_pane=True,
        secondary_agents=["claude"],
    )

    assert exit_code == 0
    assert any(call[:4] == ["tmux", "set-option", "-p", "-t"] and call[-1] == "agent:codex" for call in tmux_calls)
    assert any(call[:5] == ["tmux", "split-window", "-d", "-t", "%7"] for call in tmux_calls)
    assert any(call[:4] == ["tmux", "set-environment", "-t", "ash-codex-agent-swarm-hub"] for call in tmux_calls)
    assert any(call[:4] == ["tmux", "set-option", "-p", "-t"] and call[-1] == "manual" for call in tmux_calls)
    assert any(call[:4] == ["tmux", "set-option", "-p", "-t"] and call[-1] == "ssh:xinong" for call in tmux_calls)
    assert any(call[:4] == ["tmux", "set-option", "-p", "-t"] and call[-1] == "agent:claude" for call in tmux_calls)
    assert any(
        call[:2] == ["tmux", "split-window"] and "env -u LC_ALL -u LC_CTYPE -u LANGUAGE LANG=C LC_ALL=C LC_CTYPE=C ssh xinong" in call[-1]
        for call in tmux_calls
    )


def test_project_sessions_bridge_status_prints_session_panes_and_policy(monkeypatch, tmp_path, capsys) -> None:
    workspace = tmp_path / "agent-swarm-hub"
    workspace.mkdir()

    class FakeStore:
        def get_project(self, project_id: str):
            if project_id != "agent-swarm-hub":
                return None
            return SimpleNamespace(project_id=project_id, workspace_path=str(workspace))

    def fake_ensure_orchestrator_pane(*, project_id: str, workspace_path: str, provider: str = "claude", launch_mode: str | None = None):
        return {
            "status": "existing",
            "session_name": "ash-codex-agent-swarm-hub",
            "window_index": "1",
            "pane_id": "%0",
        }

    def fake_run(argv, check=False, capture_output=False, text=False):
        if argv and argv[0] == "tmux":
            if argv[1:3] == ["has-session", "-t"]:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if argv[1:4] == ["list-panes", "-t", "ash-codex-agent-swarm-hub:1"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout="%0\tagent:codex\tash-chat | agent-swarm-hub | codex\n%1\tmanual\tsunxiangrong.local\n",
                    stderr="",
                )
            if argv[1:3] == ["show-environment", "-t"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout=(
                        "TMUX_BRIDGE_READABLE_TARGETS=manual,agent:codex,agent:claude,ssh:xinong,ssh:ias\n"
                        "TMUX_BRIDGE_WRITABLE_TARGETS=agent:codex,agent:claude,ssh:xinong,ssh:ias\n"
                        "TMUX_BRIDGE_READONLY_PATHS=/,/etc,/usr,/var\n"
                        f"TMUX_BRIDGE_WRITABLE_PATHS={workspace}\n"
                        "TMUX_BRIDGE_DENY_PREFIXES=rm -rf,sudo,reboot,shutdown\n"
                    ),
                    stderr="",
                )
            if argv[1:4] == ["capture-pane", "-pt", "%0"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout="[agent-swarm-hub] heartbeat check: healthy existing codex process\n(base) sunxiangrong:agent-swarm-hub sunxiangrong$\n",
                    stderr="",
                )
            if argv[1:4] == ["capture-pane", "-pt", "%1"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout="manual shell ready\n",
                    stderr="",
                )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli_ops, "ProjectContextStore", lambda: FakeStore())
    monkeypatch.setattr(cli_ops, "ensure_orchestrator_pane", fake_ensure_orchestrator_pane)
    monkeypatch.setattr(cli_ops.subprocess, "run", fake_run)

    exit_code = cli_ops.project_sessions_bridge_status(
        "agent-swarm-hub",
        provider="codex",
        init=False,
        exports=True,
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Project: agent-swarm-hub" in out
    assert "Session: ash-codex-agent-swarm-hub" in out
    assert "tmux-bridge env applied: yes" in out
    assert "- %0 | agent:codex | local-shell | ash-chat | agent-swarm-hub | codex" in out
    assert "- %1 | manual | manual-readonly | sunxiangrong.local" in out
    assert "SSH Targets: xinong, ias" in out
    assert "Bridge Env:" in out
    assert "TMUX_BRIDGE_WRITABLE_TARGETS=agent:codex,agent:claude,ssh:xinong,ssh:ias" in out


def test_project_sessions_bridge_policy_updates_ssh_targets(monkeypatch, tmp_path, capsys) -> None:
    workspace = tmp_path / "agent-swarm-hub"
    workspace.mkdir()

    class FakeStore:
        def get_project(self, project_id: str):
            if project_id != "agent-swarm-hub":
                return None
            return SimpleNamespace(project_id=project_id, workspace_path=str(workspace))

    monkeypatch.setattr(cli_ops, "ProjectContextStore", lambda: FakeStore())

    exit_code = cli_ops.project_sessions_bridge_policy(
        "agent-swarm-hub",
        init=True,
        force=False,
        set_ssh_targets=["xinong", "gpu"],
    )

    assert exit_code == 0
    policy = json.loads((workspace / ".ash" / "bridge-policy.json").read_text(encoding="utf-8"))
    assert policy["ssh_targets"] == ["xinong", "gpu"]
    assert policy["readable_targets"] == ["manual", "agent:codex", "agent:claude", "ssh:xinong", "ssh:gpu"]
    assert policy["writable_targets"] == ["agent:codex", "agent:claude", "ssh:xinong", "ssh:gpu"]
    out = capsys.readouterr().out
    assert "SSH Targets: xinong, gpu" in out
    assert "Updated SSH targets." in out


def test_project_sessions_bridge_status_falls_back_to_existing_tmux_session(monkeypatch, tmp_path, capsys) -> None:
    workspace = tmp_path / "agent-swarm-hub"
    workspace.mkdir()

    class FakeStore:
        def get_project(self, project_id: str):
            if project_id != "agent-swarm-hub":
                return None
            return SimpleNamespace(project_id=project_id, workspace_path=str(workspace))

    def fake_ensure_orchestrator_pane(*, project_id: str, workspace_path: str, provider: str = "claude", launch_mode: str | None = None):
        return {
            "status": "error",
            "reason": "duplicate session: ash-codex-agent-swarm-hub",
            "window_index": "1",
        }

    def fake_run(argv, check=False, capture_output=False, text=False):
        if argv and argv[0] == "tmux":
            if argv[1:3] == ["has-session", "-t"]:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if argv[1:4] == ["list-panes", "-t", "ash-codex-agent-swarm-hub:1"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout="%0\tagent:codex\tash-chat | agent-swarm-hub | codex\n",
                    stderr="",
                )
            if argv[1:3] == ["show-environment", "-t"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout=(
                        "TMUX_BRIDGE_READABLE_TARGETS=manual,agent:codex,agent:claude,ssh:xinong,ssh:ias\n"
                        "TMUX_BRIDGE_WRITABLE_TARGETS=agent:codex,agent:claude,ssh:xinong,ssh:ias\n"
                        "TMUX_BRIDGE_READONLY_PATHS=/,/etc,/usr,/var\n"
                        f"TMUX_BRIDGE_WRITABLE_PATHS={workspace}\n"
                        "TMUX_BRIDGE_DENY_PREFIXES=rm -rf,sudo,reboot,shutdown\n"
                    ),
                    stderr="",
                )
            if argv[1:4] == ["capture-pane", "-pt", "%0"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout="(base) sunxiangrong:agent-swarm-hub sunxiangrong$\n",
                    stderr="",
                )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli_ops, "ProjectContextStore", lambda: FakeStore())
    monkeypatch.setattr(cli_ops, "ensure_orchestrator_pane", fake_ensure_orchestrator_pane)
    monkeypatch.setattr(cli_ops.subprocess, "run", fake_run)

    exit_code = cli_ops.project_sessions_bridge_status(
        "agent-swarm-hub",
        provider="codex",
        init=False,
        exports=False,
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Session: ash-codex-agent-swarm-hub" in out
    assert "tmux-bridge env applied: yes" in out
    assert "- %0 | agent:codex | local-shell | ash-chat | agent-swarm-hub | codex" in out


def test_project_sessions_bridge_workbench_opens_tmux_then_prints_status(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_open_tmux(project_id: str, *, provider: str | None, bridge_layout: bool, ssh_targets: list[str] | None) -> int:
        raise AssertionError("stale fake signature")
        return 0

    def fake_open_tmux(
        project_id: str,
        *,
        provider: str | None,
        bridge_layout: bool,
        ssh_targets: list[str] | None,
        manual_pane: bool,
        secondary_agents: list[str] | None,
    ) -> int:
        calls.append(("open", project_id, provider, bridge_layout, list(ssh_targets or []), manual_pane, list(secondary_agents or [])))
        return 0

    def fake_bridge_status(project_id: str, *, provider: str | None, init: bool, exports: bool) -> int:
        calls.append(("status", project_id, provider, init, exports))
        return 0

    monkeypatch.setattr(cli_ops, "project_sessions_open_tmux_terminal", fake_open_tmux)
    monkeypatch.setattr(cli_ops, "project_sessions_bridge_status", fake_bridge_status)

    exit_code = cli_ops.project_sessions_bridge_workbench(
        "agent-swarm-hub",
        provider="codex",
        ssh_targets=["xinong", "ias"],
        manual_pane=False,
        secondary_agents=["claude"],
        init=True,
        exports=True,
    )

    assert exit_code == 0
    assert calls == [
        ("open", "agent-swarm-hub", "codex", True, ["xinong", "ias"], False, ["claude"]),
        ("status", "agent-swarm-hub", "codex", True, True),
    ]


def test_project_sessions_bridge_workbench_uses_policy_default_ssh_targets(monkeypatch, tmp_path) -> None:
    workspace = tmp_path / "agent-swarm-hub"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / ".ash").mkdir(parents=True, exist_ok=True)
    (workspace / ".ash" / "bridge-policy.json").write_text(
        json.dumps(
            {
                "project_id": "agent-swarm-hub",
                "workspace_path": str(workspace),
                "ssh_targets": ["xinong", "gpu"],
                "readable_targets": ["manual", "agent:codex", "agent:claude", "ssh:xinong", "ssh:gpu"],
                "writable_targets": ["agent:codex", "agent:claude", "ssh:xinong", "ssh:gpu"],
                "readonly_paths": ["/", "/etc", "/usr", "/var"],
                "writable_paths": [str(workspace)],
                "deny_prefixes": ["rm -rf", "sudo", "reboot", "shutdown"],
                "allow_manual_write": False,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class FakeStore:
        def get_project(self, project_id: str):
            if project_id != "agent-swarm-hub":
                return None
            return SimpleNamespace(project_id=project_id, workspace_path=str(workspace))

    calls: list[tuple[str, object]] = []

    def fake_open_tmux(
        project_id: str,
        *,
        provider: str | None,
        bridge_layout: bool,
        ssh_targets: list[str] | None,
        manual_pane: bool,
        secondary_agents: list[str] | None,
    ) -> int:
        calls.append(("open", project_id, provider, bridge_layout, list(ssh_targets or []), manual_pane, list(secondary_agents or [])))
        return 0

    def fake_bridge_status(project_id: str, *, provider: str | None, init: bool, exports: bool) -> int:
        calls.append(("status", project_id, provider, init, exports))
        return 0

    monkeypatch.setattr(cli_ops, "ProjectContextStore", lambda: FakeStore())
    monkeypatch.setattr(cli_ops, "project_sessions_open_tmux_terminal", fake_open_tmux)
    monkeypatch.setattr(cli_ops, "project_sessions_bridge_status", fake_bridge_status)

    exit_code = cli_ops.project_sessions_bridge_workbench(
        "agent-swarm-hub",
        provider="codex",
        ssh_targets=[],
        manual_pane=True,
        secondary_agents=[],
        init=False,
        exports=False,
    )

    assert exit_code == 0
    assert calls == [
        ("open", "agent-swarm-hub", "codex", True, ["xinong", "gpu"], True, []),
        ("status", "agent-swarm-hub", "codex", False, False),
    ]


def test_project_context_sync_updates_structured_project_summary(tmp_path) -> None:
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    workspace_path = tmp_path / "agent-browser"
    workspace_path.mkdir()

    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            CREATE TABLE project_memory (
                project_id TEXT PRIMARY KEY,
                focus TEXT NOT NULL DEFAULT '',
                recent_context TEXT NOT NULL DEFAULT '',
                memory TEXT NOT NULL DEFAULT '',
                recent_hints_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE provider_bindings (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, provider)
            );
            CREATE TABLE project_sessions (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                title TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, session_id)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "agent-browser",
                "agent-browser",
                str(workspace_path),
                "Browser automation exploration",
                "Project: agent-browser\nCurrent focus: stale\nRecent context: stale",
            ),
        )

    from agent_swarm_hub.project_context import ProjectContextStore

    store = ProjectContextStore(str(shared_db_path))
    store.upsert_project_memory(
        "agent-browser",
        focus="chrome会做的更好吗",
        recent_context="已经确认当前问题是项目级上下文摘要过度退化",
        memory="Compare whether Chrome-native tooling would produce a more reliable browser workflow.",
        recent_hints=["user: 整理项目级长期记忆"],
    )
    store.sync_project_summary("agent-browser")

    with sqlite3.connect(shared_db_path) as conn:
        summary = conn.execute(
            "SELECT summary FROM projects WHERE project_id = ?",
            ("agent-browser",),
        ).fetchone()[0]

    assert "Current focus: chrome会做的更好吗" in summary
    assert "Current state: 已经确认当前问题是项目级上下文摘要过度退化" in summary
    assert "Next step: 整理项目级长期记忆" in summary
    assert "Cache summary: Compare whether Chrome-native tooling would produce a more reliable browser workflow." in summary


def test_cli_local_native_rejects_workspace_without_path(monkeypatch, tmp_path, capsys) -> None:
    db_path = tmp_path / "sessions.sqlite3"

    from agent_swarm_hub.session_store import SessionStore

    store = SessionStore(db_path)
    store.upsert_workspace(
        workspace_id="project-alpha",
        title="project-alpha",
        path="",
        backend="codex",
        transport="direct",
    )

    monkeypatch.setenv("ASH_SESSION_DB", str(db_path))
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "local-native", "--provider", "codex", "--project", "project-alpha"])

    exit_code = main()
    err = capsys.readouterr().err

    assert exit_code == 2
    assert "has no enterable path" in err


def test_cli_project_sessions_current_and_list(monkeypatch, tmp_path, capsys) -> None:
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_bindings (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, provider)
            );
            CREATE TABLE project_sessions (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                title TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, session_id)
            );
            """
        )
        conn.execute("INSERT INTO projects (project_id, title, workspace_path) VALUES (?, ?, ?)", ("post-gwas", "post-gwas", "/tmp/post-gwas"))
        conn.execute("INSERT INTO provider_bindings (project_id, provider, raw_session_id) VALUES (?, ?, ?)", ("post-gwas", "codex", "codex-current"))
        conn.execute(
            "INSERT INTO project_sessions (project_id, provider, session_id, status, title, summary, last_used_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("post-gwas", "codex", "codex-current", "active", "Current task", "Current task summary", "2026-03-18T10:00:00Z"),
        )
        conn.execute(
            "INSERT INTO project_sessions (project_id, provider, session_id, status, title, summary, last_used_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("post-gwas", "codex", "codex-old", "archived", "Old task", "Old task summary", "2026-03-17T10:00:00Z"),
        )

    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "project-sessions", "current", "post-gwas"])
    exit_code = main()
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "codex: codex-current" in output

    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "project-sessions", "list", "post-gwas"])
    exit_code = main()
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "codex | current | codex-current" in output
    assert "codex | archived | codex-old" in output


def test_cli_project_sessions_sync_memory(monkeypatch, tmp_path, capsys) -> None:
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    workspace_path = tmp_path / "agent-browser"
    workspace_path.mkdir()
    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE project_memory (
                project_id TEXT PRIMARY KEY,
                focus TEXT NOT NULL DEFAULT '',
                recent_context TEXT NOT NULL DEFAULT '',
                memory TEXT NOT NULL DEFAULT '',
                recent_hints_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            "INSERT INTO projects (project_id, title, workspace_path, profile, summary) VALUES (?, ?, ?, ?, ?)",
            ("agent-browser", "agent-browser", str(workspace_path), "Browser automation exploration", "stale"),
        )
        conn.execute(
            "INSERT INTO project_memory (project_id, focus, recent_context, memory, recent_hints_json) VALUES (?, ?, ?, ?, ?)",
            (
                "agent-browser",
                "chrome会做的更好吗",
                "已经确认当前问题是项目级上下文摘要过度退化",
                "Compare whether Chrome-native tooling would produce a more reliable browser workflow.",
                json.dumps(["user: 整理项目级长期记忆"], ensure_ascii=False),
            ),
        )

    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    ov_sync = {}
    imports_root = tmp_path / "ov-imports"
    class FakeMemoryExecutor:
        def run(self, prompt: str):
            return SimpleNamespace(
                output=json.dumps(
                    {
                        "focus": "chrome会做的更好吗",
                        "current_state": "已经确认当前问题是项目级上下文摘要过度退化",
                        "next_step": "整理项目级长期记忆",
                        "long_term_memory": "Compare whether Chrome-native tooling would produce a more reliable browser workflow.",
                        "key_points": ["整理项目级长期记忆"],
                    },
                    ensure_ascii=False,
                )
            )
    monkeypatch.setattr("agent_swarm_hub.project_context.build_executor_for_config", lambda **_: FakeMemoryExecutor())

    def fake_ov_sync(project_id: str) -> None:
        ov_sync["project_id"] = project_id
        project_root = imports_root / project_id
        (project_root / "runtime").mkdir(parents=True, exist_ok=True)
        (project_root / "README.md").write_text("# agent-browser\n\nOV project tree.\n", encoding="utf-8")
        (project_root / "runtime" / "memory_bundle.md").write_text("OV says this project memory just synced.\n", encoding="utf-8")

    monkeypatch.setattr("agent_swarm_hub.cli._sync_openviking_project_artifacts", fake_ov_sync)
    monkeypatch.setattr("agent_swarm_hub.openviking_support.DEFAULT_IMPORT_TREE_ROOT", imports_root)
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "project-sessions", "sync-memory", "agent-browser"])
    exit_code = main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Synced project memory for `agent-browser`." in output
    assert ov_sync["project_id"] == "agent-browser"
    assert (workspace_path / "PROJECT_MEMORY.md").exists()
    assert (workspace_path / "PROJECT_SKILL.md").exists()
    assert "## OpenViking Overview" in (workspace_path / "PROJECT_MEMORY.md").read_text(encoding="utf-8")
    assert "OV says this project memory just synced." in (workspace_path / "PROJECT_MEMORY.md").read_text(encoding="utf-8")
    assert "## OpenViking Context Notes" in (workspace_path / "PROJECT_SKILL.md").read_text(encoding="utf-8")
    assert "OV says this project memory just synced." in (workspace_path / "PROJECT_SKILL.md").read_text(encoding="utf-8")
    with sqlite3.connect(shared_db_path) as conn:
        summary = conn.execute("SELECT summary FROM projects WHERE project_id = ?", ("agent-browser",)).fetchone()[0]
    assert "Current focus: chrome会做的更好吗" in summary
    assert "Current state: 已经确认当前问题是项目级上下文摘要过度退化" in summary


def test_cli_project_sessions_sync_memory_all(monkeypatch, tmp_path, capsys) -> None:
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    workspace_a = tmp_path / "alpha"
    workspace_b = tmp_path / "beta"
    workspace_a.mkdir()
    workspace_b.mkdir()
    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE project_memory (
                project_id TEXT PRIMARY KEY,
                focus TEXT NOT NULL DEFAULT '',
                recent_context TEXT NOT NULL DEFAULT '',
                memory TEXT NOT NULL DEFAULT '',
                recent_hints_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.executemany(
            "INSERT INTO projects (project_id, title, workspace_path, profile, summary) VALUES (?, ?, ?, ?, ?)",
            [
                ("alpha", "alpha", str(workspace_a), "", "stale"),
                ("beta", "beta", str(workspace_b), "", "stale"),
            ],
        )
        conn.executemany(
            "INSERT INTO project_memory (project_id, focus, recent_context, memory, recent_hints_json) VALUES (?, ?, ?, ?, ?)",
            [
                ("alpha", "Focus alpha", "State alpha", "Memory alpha", "[]"),
                ("beta", "Focus beta", "State beta", "Memory beta", "[]"),
            ],
        )

    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    class FakeMemoryExecutor:
        def run(self, prompt: str):
            target = "alpha" if "Project: alpha" in prompt else "beta"
            return SimpleNamespace(
                output=json.dumps(
                    {
                        "focus": f"Focus {target}",
                        "current_state": f"State {target}",
                        "next_step": f"Next {target}",
                        "long_term_memory": f"Memory {target}",
                        "key_points": [f"Next {target}"],
                    },
                    ensure_ascii=False,
                )
            )
    monkeypatch.setattr("agent_swarm_hub.project_context.build_executor_for_config", lambda **_: FakeMemoryExecutor())
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "project-sessions", "sync-memory", "--all"])
    exit_code = main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Synced project memory for `alpha`." in output
    assert "Synced project memory for `beta`." in output
    assert (workspace_a / "PROJECT_MEMORY.md").exists()
    assert (workspace_b / "PROJECT_MEMORY.md").exists()


def test_cli_project_sessions_use_switches_binding(monkeypatch, tmp_path, capsys) -> None:
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    workspace_path = tmp_path / "post-gwas"
    workspace_path.mkdir()
    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_bindings (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, provider)
            );
            CREATE TABLE project_sessions (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                title TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, session_id)
            );
            """
        )
        conn.execute("INSERT INTO projects (project_id, title, workspace_path) VALUES (?, ?, ?)", ("post-gwas", "post-gwas", str(workspace_path)))
        conn.execute("INSERT INTO provider_bindings (project_id, provider, raw_session_id) VALUES (?, ?, ?)", ("post-gwas", "codex", "codex-old"))
        conn.execute(
            "INSERT INTO project_sessions (project_id, provider, session_id, status, title, last_used_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("post-gwas", "codex", "codex-old", "archived", "Old task", "2026-03-17T10:00:00Z"),
        )
        conn.execute(
            "INSERT INTO project_sessions (project_id, provider, session_id, status, title, last_used_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("post-gwas", "codex", "codex-new", "active", "New task", "2026-03-18T10:00:00Z"),
        )

    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "project-sessions", "use", "post-gwas", "codex", "codex-new"])
    exit_code = main()
    output = capsys.readouterr().out
    assert exit_code == 0
    assert "Current codex session for `post-gwas` set to codex-new." in output

    with sqlite3.connect(shared_db_path) as conn:
        binding = conn.execute(
            "SELECT raw_session_id FROM provider_bindings WHERE project_id = ? AND provider = ?",
            ("post-gwas", "codex"),
        ).fetchone()
        statuses = conn.execute(
            "SELECT session_id, status FROM project_sessions WHERE project_id = ? AND provider = ? ORDER BY session_id",
            ("post-gwas", "codex"),
        ).fetchall()
        summary = conn.execute(
            "SELECT summary FROM projects WHERE project_id = ?",
            ("post-gwas",),
        ).fetchone()[0]

    assert binding == ("codex-new",)
    assert statuses == [("codex-new", "active"), ("codex-old", "archived")]
    assert "Current sessions: codex=codex-new" in summary
    assert (workspace_path / "PROJECT_MEMORY.md").exists()


def test_cli_project_sessions_remove_project(monkeypatch, tmp_path, capsys) -> None:
    shared_db_path = tmp_path / "shared-projects.sqlite3"
    runtime_db_path = tmp_path / "runtime.sqlite3"
    workspace_path = tmp_path / "deleted-project"
    with sqlite3.connect(shared_db_path) as conn:
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
            CREATE TABLE provider_bindings (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, provider)
            );
            CREATE TABLE project_memory (
                project_id TEXT PRIMARY KEY,
                focus TEXT NOT NULL DEFAULT '',
                recent_context TEXT NOT NULL DEFAULT '',
                memory TEXT NOT NULL DEFAULT '',
                recent_hints_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE provider_sessions (
                provider TEXT NOT NULL,
                raw_session_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, raw_session_id)
            );
            CREATE TABLE project_sessions (
                project_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                title TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                cwd TEXT NOT NULL DEFAULT '',
                source_path TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (provider, session_id)
            );
            CREATE TABLE dashboard_project_pins (
                project_id TEXT PRIMARY KEY,
                pinned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.execute(
            "INSERT INTO projects (project_id, title, workspace_path) VALUES (?, ?, ?)",
            ("deleted-project", "deleted-project", str(workspace_path)),
        )
        conn.execute(
            "INSERT INTO provider_bindings (project_id, provider, raw_session_id) VALUES (?, ?, ?)",
            ("deleted-project", "codex", "codex-session"),
        )
        conn.execute(
            "INSERT INTO project_memory (project_id, focus, recent_context, memory, recent_hints_json) VALUES (?, ?, ?, ?, ?)",
            ("deleted-project", "Focus", "State", "Memory", "[]"),
        )
        conn.execute(
            "INSERT INTO provider_sessions (provider, raw_session_id, project_id) VALUES (?, ?, ?)",
            ("codex", "codex-session", "deleted-project"),
        )
        conn.execute(
            "INSERT INTO project_sessions (project_id, provider, session_id) VALUES (?, ?, ?)",
            ("deleted-project", "codex", "codex-session"),
        )
        conn.execute(
            "INSERT INTO dashboard_project_pins (project_id) VALUES (?)",
            ("deleted-project",),
        )

    runtime_store = SessionStore(runtime_db_path)
    runtime_store.upsert_workspace(
        workspace_id="deleted-project",
        title="deleted-project",
        path=str(workspace_path),
        backend="codex",
        transport="direct",
    )
    runtime_store.upsert_task(
        task_id="task-1",
        session_key="local-cli",
        workspace_id="deleted-project",
        title="Task",
        status="open",
    )
    runtime_store.upsert_session(
        session_key="local-cli",
        platform="local",
        chat_id="local-user",
        thread_id=None,
        active_task_id="task-1",
        executor_session_id="exec-1",
        conversation_summary="summary",
        swarm_state_json="",
        escalations_json="[]",
    )
    runtime_store.append_message(
        session_key="local-cli",
        role="user",
        text="hello",
        task_id="task-1",
    )
    runtime_store.upsert_workspace_session(
        session_key="local-cli",
        workspace_id="deleted-project",
        active_task_id="task-1",
        executor_session_id="exec-1",
        claude_session_id="",
        codex_session_id="codex-session",
        phase="discussion",
        conversation_summary="summary",
        swarm_state_json="",
        escalations_json="[]",
    )
    runtime_store.append_task_handoff(
        session_key="local-cli",
        workspace_id="deleted-project",
        task_id="task-1",
        handoff_type="execution_plan",
        source_agent="claude",
        target_agent="codex",
        content_json=json.dumps({"planner_output": "plan"}, ensure_ascii=False),
    )
    runtime_store.bind_chat(
        session_key="local-cli",
        platform="local",
        chat_id="local-user",
        thread_id=None,
        workspace_id="deleted-project",
    )

    monkeypatch.setenv("ASH_PROJECT_SESSION_DB", str(shared_db_path))
    monkeypatch.setenv("ASH_SESSION_DB", str(runtime_db_path))
    monkeypatch.setattr("sys.argv", ["agent-swarm-hub", "project-sessions", "remove-project", "deleted-project"])
    exit_code = main()
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Removed stale project records for `deleted-project`." in output

    with sqlite3.connect(shared_db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM projects WHERE project_id = ?", ("deleted-project",)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM provider_bindings WHERE project_id = ?", ("deleted-project",)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM project_memory WHERE project_id = ?", ("deleted-project",)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM provider_sessions WHERE project_id = ?", ("deleted-project",)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM project_sessions WHERE project_id = ?", ("deleted-project",)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM dashboard_project_pins WHERE project_id = ?", ("deleted-project",)).fetchone()[0] == 0

    with sqlite3.connect(runtime_db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM workspaces WHERE workspace_id = ?", ("deleted-project",)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM workspace_sessions WHERE workspace_id = ?", ("deleted-project",)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM tasks WHERE workspace_id = ?", ("deleted-project",)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM task_handoffs WHERE workspace_id = ?", ("deleted-project",)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM chat_bindings WHERE workspace_id = ?", ("deleted-project",)).fetchone()[0] == 0
