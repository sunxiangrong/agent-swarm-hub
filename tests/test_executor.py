import threading

from agent_swarm_hub import (
    AskExecutor,
    EchoExecutor,
    ExecutionResult,
    Executor,
    ExecutorBusyError,
    FallbackExecutor,
    LocalExecutorSessionPool,
    build_executor,
    build_executor_for_config,
)


def test_echo_executor_returns_prompt() -> None:
    result = EchoExecutor().run("hello world")

    assert result.backend == "echo"
    assert result.output == "hello world"


def test_ask_executor_runs_provider_via_ask(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return type("Proc", (), {"returncode": 0, "stdout": "reply from ask\n", "stderr": ""})()

    monkeypatch.setattr("subprocess.run", fake_run)

    result = AskExecutor(provider="claude", command="/tmp/ask", timeout_s=42).run("hello ask")

    assert captured["cmd"] == ["/tmp/ask", "claude", "--foreground", "--timeout", "42", "hello ask"]
    assert captured["env"]["CCB_CALLER"] == "manual"
    assert result.backend == "claude"
    assert result.output == "reply from ask"


def test_ask_executor_passes_ccb_env(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["env"] = kwargs["env"]
        return type("Proc", (), {"returncode": 0, "stdout": "ok\n", "stderr": ""})()

    monkeypatch.setattr("subprocess.run", fake_run)

    AskExecutor(
        provider="codex",
        command="/tmp/ask",
        work_dir="/tmp/project",
        extra_env={"CCB_SESSION_ID": "codex-abc123"},
    ).run("hello")

    assert captured["env"]["CCB_CALLER"] == "manual"
    assert captured["env"]["CCB_WORK_DIR"] == "/tmp/project"
    assert captured["env"]["CCB_RUN_DIR"] == "/tmp/project"
    assert captured["env"]["CCB_SESSION_ID"] == "codex-abc123"


def test_build_executor_prefers_ask_for_codex_when_available(monkeypatch) -> None:
    monkeypatch.setenv("ASH_EXECUTOR", "codex")
    monkeypatch.delenv("ASH_EXECUTOR_TRANSPORT", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "/tmp/askd" if name == "askd" else "/tmp/ask" if name == "ask" else None)

    executor = build_executor()

    assert isinstance(executor, FallbackExecutor)


def test_build_executor_falls_back_to_direct_when_askd_missing(monkeypatch) -> None:
    monkeypatch.setenv("ASH_EXECUTOR", "claude")
    monkeypatch.delenv("ASH_EXECUTOR_TRANSPORT", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "/tmp/ask" if name == "ask" else None)
    monkeypatch.setattr("pathlib.Path.exists", lambda self: False)

    executor = build_executor()

    assert executor.__class__.__name__ == "ClaudePrintExecutor"


def test_build_executor_defaults_claude_to_direct_transport(monkeypatch) -> None:
    monkeypatch.setenv("ASH_EXECUTOR", "claude")
    monkeypatch.delenv("ASH_EXECUTOR_TRANSPORT", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "/tmp/askd" if name == "askd" else "/tmp/ask" if name == "ask" else None)

    executor = build_executor()

    assert executor.__class__.__name__ == "ClaudePrintExecutor"


def test_build_executor_for_config_uses_workspace_settings(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/tmp/askd" if name == "askd" else "/tmp/ask" if name == "ask" else None)

    executor = build_executor_for_config(mode="codex", transport="ask", work_dir="/tmp/project")

    assert isinstance(executor, FallbackExecutor)


def test_build_executor_for_config_supports_ccb_transport(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/tmp/askd" if name == "askd" else "/tmp/ask" if name == "ask" else None)

    executor = build_executor_for_config(
        mode="claude",
        transport="ccb",
        work_dir="/tmp/project",
        extra_env={"CCB_SESSION_ID": "claude-abc123"},
    )

    assert isinstance(executor, FallbackExecutor)


def test_local_executor_session_pool_rejects_concurrent_run() -> None:
    gate = threading.Event()
    release = threading.Event()

    class SlowExecutor(Executor):
        def run(self, prompt: str) -> ExecutionResult:
            gate.set()
            release.wait(timeout=1)
            return ExecutionResult(output=prompt, backend="slow")

    pool = LocalExecutorSessionPool()
    executor = SlowExecutor()
    errors = []

    def first_run():
        pool.run(
            executor_session_id="exec-1",
            prompt="hello",
            mode="echo",
            transport="direct",
            work_dir=None,
            executor_override=executor,
        )

    thread = threading.Thread(target=first_run)
    thread.start()
    gate.wait(timeout=1)

    try:
        pool.run(
            executor_session_id="exec-1",
            prompt="world",
            mode="echo",
            transport="direct",
            work_dir=None,
            executor_override=executor,
        )
    except ExecutorBusyError as exc:
        errors.append(str(exc))
    finally:
        release.set()
        thread.join(timeout=1)

    assert errors
    assert "busy" in errors[0].lower()
