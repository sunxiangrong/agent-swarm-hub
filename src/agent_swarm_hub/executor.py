from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class ExecutorError(RuntimeError):
    pass


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
        return ExecutionResult(output=output, backend="codex")


def build_executor(work_dir: str | None = None) -> Executor:
    mode = (os.getenv("ASH_EXECUTOR") or "codex").strip().lower()
    timeout_s = int((os.getenv("ASH_EXECUTOR_TIMEOUT_S") or "120").strip() or "120")
    if mode == "claude":
        return ClaudePrintExecutor(work_dir=work_dir, timeout_s=timeout_s)
    if mode == "echo":
        return EchoExecutor()
    return CodexExecExecutor(work_dir=work_dir, timeout_s=timeout_s)
