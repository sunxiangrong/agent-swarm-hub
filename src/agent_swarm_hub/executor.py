from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
import json


class ExecutorError(RuntimeError):
    pass


class ConfirmationRequiredError(ExecutorError):
    def __init__(self, *, prompt: str, agent: str, kind: str):
        super().__init__(prompt)
        self.prompt = prompt
        self.agent = agent
        self.kind = kind


class AuthenticationRequiredError(ExecutorError):
    def __init__(self, *, prompt: str, agent: str):
        super().__init__(prompt)
        self.prompt = prompt
        self.agent = agent


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    output: str
    backend: str


class Executor:
    def run(self, prompt: str) -> ExecutionResult:
        raise NotImplementedError


class EchoExecutor(Executor):
    def run(self, prompt: str) -> ExecutionResult:
        return ExecutionResult(output=prompt, backend="echo")


class AskExecutor(Executor):
    def __init__(
        self,
        provider: str,
        command: str | None = None,
        work_dir: str | None = None,
        timeout_s: int = 120,
        extra_env: dict[str, str] | None = None,
    ):
        self.provider = provider
        self.command = command or os.getenv("ASH_ASK_BIN", "").strip() or shutil.which("ask") or str(Path.home() / ".local/bin/ask")
        self.work_dir = work_dir
        self.timeout_s = timeout_s
        self.extra_env = dict(extra_env or {})

    def run(self, prompt: str) -> ExecutionResult:
        print(f"[agent-swarm-hub] executor=ask provider={self.provider} starting", file=sys.stderr, flush=True)
        env = os.environ.copy()
        env.setdefault("CCB_CALLER", "manual")
        if self.work_dir:
            env.setdefault("CCB_WORK_DIR", self.work_dir)
            env.setdefault("CCB_RUN_DIR", self.work_dir)
        env.update({key: value for key, value in self.extra_env.items() if value})
        proc = subprocess.run(
            [self.command, self.provider, "--foreground", "--timeout", str(self.timeout_s), prompt],
            cwd=self.work_dir or None,
            capture_output=True,
            text=True,
            timeout=self.timeout_s + 15,
            env=env,
        )
        marker = "[CCB_CONFIRMATION_REQUIRED]"
        auth_marker = "[CCB_AUTHENTICATION_REQUIRED]"
        stdout_text = (proc.stdout or "").strip()
        stderr_text = (proc.stderr or "").strip()
        for stream_text in (stdout_text, stderr_text):
            if marker in stream_text:
                payload_text = stream_text.split(marker, 1)[1].strip()
                try:
                    payload = json.loads(payload_text)
                except Exception:
                    payload = {}
                raise ConfirmationRequiredError(
                    prompt=str(payload.get("prompt") or "Confirmation required."),
                    agent=str(payload.get("agent") or self.provider),
                    kind=str(payload.get("kind") or ""),
                )
            if auth_marker in stream_text:
                payload_text = stream_text.split(auth_marker, 1)[1].strip()
                try:
                    payload = json.loads(payload_text)
                except Exception:
                    payload = {}
                raise AuthenticationRequiredError(
                    prompt=str(payload.get("prompt") or "Authentication required."),
                    agent=str(payload.get("agent") or self.provider),
                )
        if proc.returncode != 0:
            raise ExecutorError(stderr_text or stdout_text or f"ask {self.provider} failed")
        output = stdout_text
        if not output:
            raise ExecutorError(f"ask {self.provider} returned no final message")
        print(f"[agent-swarm-hub] executor=ask provider={self.provider} completed", file=sys.stderr, flush=True)
        return ExecutionResult(output=output, backend=self.provider)


class FallbackExecutor(Executor):
    def __init__(self, primary: Executor, fallback: Executor):
        self.primary = primary
        self.fallback = fallback

    def run(self, prompt: str) -> ExecutionResult:
        try:
            return self.primary.run(prompt)
        except ConfirmationRequiredError:
            raise
        except AuthenticationRequiredError:
            raise
        except ExecutorError as exc:
            print(f"[agent-swarm-hub] primary executor failed, falling back: {exc}", file=sys.stderr, flush=True)
            return self.fallback.run(prompt)


def send_bridge_confirmation(*, mode: str, work_dir: str | None) -> bool:
    if not work_dir:
        return False
    lib_dir = Path(os.getenv("ASH_CCB_LIB_DIR", "/Users/sunxiangrong/Desktop/CLI/Codex/claude_code_bridge/lib"))
    if not lib_dir.exists():
        return False

    cwd_before = Path.cwd()
    try:
        os.chdir(work_dir)
        sys.path.insert(0, str(lib_dir))
        if mode == "claude":
            from claude_comm import ClaudeCommunicator  # type: ignore

            comm = ClaudeCommunicator(lazy_init=True)
            backend = comm.backend
            pane_id = comm.pane_id
        else:
            from caskd_session import load_project_session  # type: ignore

            session = load_project_session(Path(work_dir))
            if not session:
                return False
            ok, pane_id = session.ensure_pane()
            if not ok:
                return False
            backend = session.backend()
        if not backend or not pane_id:
            return False
        send_key = getattr(backend, "send_key", None)
        if not callable(send_key):
            return False
        return bool(send_key(str(pane_id), "Enter"))
    finally:
        try:
            sys.path = [p for p in sys.path if p != str(lib_dir)]
        except Exception:
            pass
        os.chdir(cwd_before)


class ClaudePrintExecutor(Executor):
    def __init__(self, command: str | None = None, work_dir: str | None = None, timeout_s: int = 120):
        self.command = command or os.getenv("ASH_CLAUDE_BIN", str(Path.home() / ".local/bin/claude"))
        self.work_dir = work_dir
        self.timeout_s = timeout_s

    def run(self, prompt: str) -> ExecutionResult:
        proc = subprocess.run(
            [self.command, "-p", "--dangerously-skip-permissions", prompt],
            cwd=self.work_dir or None,
            capture_output=True,
            text=True,
            timeout=self.timeout_s,
        )
        if proc.returncode != 0:
            raise ExecutorError(proc.stderr.strip() or proc.stdout.strip() or "claude -p failed")
        return ExecutionResult(output=proc.stdout.strip(), backend="claude")


class CodexExecExecutor(Executor):
    def __init__(self, command: str | None = None, work_dir: str | None = None, timeout_s: int = 120):
        self.command = command or os.getenv("ASH_CODEX_BIN", str(Path.home() / ".local/bin/codex"))
        self.work_dir = work_dir
        self.timeout_s = timeout_s

    def run(self, prompt: str) -> ExecutionResult:
        print("[agent-swarm-hub] executor=codex starting", file=sys.stderr, flush=True)
        with tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".txt") as handle:
            output_path = handle.name
        proc = subprocess.run(
            [
                self.command,
                "exec",
                "--skip-git-repo-check",
                "--sandbox",
                "workspace-write",
                "--output-last-message",
                output_path,
                prompt,
            ],
            cwd=self.work_dir or None,
            capture_output=True,
            text=True,
            timeout=self.timeout_s,
        )
        if proc.returncode != 0:
            raise ExecutorError(proc.stderr.strip() or proc.stdout.strip() or "codex exec failed")
        output = Path(output_path).read_text(encoding="utf-8", errors="replace").strip()
        if not output:
            raise ExecutorError("codex exec returned no final message")
        print("[agent-swarm-hub] executor=codex completed", file=sys.stderr, flush=True)
        return ExecutionResult(output=output, backend="codex")


def _askd_available(ask_command: str) -> bool:
    explicit = os.getenv("ASH_ASKD_BIN", "").strip()
    if explicit:
        return Path(explicit).exists()
    found = shutil.which("askd")
    if found:
        return True
    sibling = Path(ask_command).resolve().with_name("askd")
    return sibling.exists()


def build_executor_for_config(
    *,
    mode: str,
    transport: str,
    work_dir: str | None = None,
    timeout_s: int | None = None,
    extra_env: dict[str, str] | None = None,
) -> Executor:
    mode = (mode or "codex").strip().lower()
    transport = (transport or "auto").strip().lower()
    timeout_s = timeout_s if timeout_s is not None else int((os.getenv("ASH_EXECUTOR_TIMEOUT_S") or "120").strip() or "120")
    if mode == "claude" and transport == "auto":
        transport = "direct"
    if mode in {"claude", "codex"} and transport != "direct":
        ask_command = os.getenv("ASH_ASK_BIN", "").strip() or shutil.which("ask")
        if ask_command and _askd_available(ask_command):
            fallback: Executor
            if mode == "claude":
                fallback = ClaudePrintExecutor(work_dir=work_dir, timeout_s=timeout_s)
            else:
                fallback = CodexExecExecutor(work_dir=work_dir, timeout_s=timeout_s)
            return FallbackExecutor(
                primary=AskExecutor(
                    provider=mode,
                    command=ask_command,
                    work_dir=work_dir,
                    timeout_s=timeout_s,
                    extra_env=extra_env,
                ),
                fallback=fallback,
            )
    if mode == "claude":
        return ClaudePrintExecutor(work_dir=work_dir, timeout_s=timeout_s)
    if mode == "echo":
        return EchoExecutor()
    return CodexExecExecutor(work_dir=work_dir, timeout_s=timeout_s)


def build_executor(work_dir: str | None = None) -> Executor:
    return build_executor_for_config(
        mode=(os.getenv("ASH_EXECUTOR") or "codex").strip().lower(),
        transport=(os.getenv("ASH_EXECUTOR_TRANSPORT") or "auto").strip().lower(),
        work_dir=work_dir,
    )
