from __future__ import annotations

import threading
from dataclasses import dataclass, field
from time import time

from .executor import ExecutionResult, Executor, ExecutorError, build_executor_for_config


class ExecutorBusyError(ExecutorError):
    pass


@dataclass(slots=True)
class LocalExecutorSession:
    executor_session_id: str
    executor: Executor
    signature: tuple[str, str, str | None, int | None]
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_used_at: float = field(default_factory=time)


class LocalExecutorSessionPool:
    def __init__(self):
        self._sessions: dict[str, LocalExecutorSession] = {}
        self._guard = threading.Lock()

    def run(
        self,
        *,
        executor_session_id: str,
        prompt: str,
        mode: str,
        transport: str,
        work_dir: str | None,
        executor_override: Executor | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        session = self._get_or_create_session(
            executor_session_id=executor_session_id,
            mode=mode,
            transport=transport,
            work_dir=work_dir,
            executor_override=executor_override,
            extra_env=extra_env,
        )
        if not session.lock.acquire(blocking=False):
            raise ExecutorBusyError(f"Executor session {executor_session_id} is busy")
        try:
            result = session.executor.run(prompt)
            session.last_used_at = time()
            return result
        finally:
            session.lock.release()

    def _get_or_create_session(
        self,
        *,
        executor_session_id: str,
        mode: str,
        transport: str,
        work_dir: str | None,
        executor_override: Executor | None,
        extra_env: dict[str, str] | None,
    ) -> LocalExecutorSession:
        env_signature = tuple(sorted((extra_env or {}).items()))
        signature = (mode, transport, work_dir, id(executor_override) if executor_override is not None else None, env_signature)
        with self._guard:
            existing = self._sessions.get(executor_session_id)
            if existing is not None and existing.signature == signature:
                return existing
            executor = executor_override or build_executor_for_config(
                mode=mode,
                transport=transport,
                work_dir=work_dir,
                extra_env=extra_env,
            )
            session = LocalExecutorSession(
                executor_session_id=executor_session_id,
                executor=executor,
                signature=signature,
            )
            self._sessions[executor_session_id] = session
            return session
