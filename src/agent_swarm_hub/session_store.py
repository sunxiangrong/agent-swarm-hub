from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class ChatSessionRecord:
    session_key: str
    platform: str
    chat_id: str
    thread_id: str | None
    active_task_id: str | None
    executor_session_id: str | None
    conversation_summary: str
    swarm_state_json: str
    escalations_json: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class WorkspaceRecord:
    workspace_id: str
    title: str
    path: str
    backend: str
    transport: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ChatBindingRecord:
    session_key: str
    platform: str
    chat_id: str
    thread_id: str | None
    workspace_id: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class WorkspaceSessionRecord:
    session_key: str
    workspace_id: str
    active_task_id: str | None
    executor_session_id: str | None
    claude_session_id: str | None
    codex_session_id: str | None
    phase: str
    conversation_summary: str
    swarm_state_json: str
    escalations_json: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class TaskRecord:
    task_id: str
    session_key: str
    workspace_id: str
    title: str
    status: str
    executor_session_id: str | None
    last_checkpoint: str
    created_at: str
    updated_at: str
    closed_at: str | None


class SessionStore:
    def __init__(self, db_path: str | os.PathLike[str] | None = None):
        raw_path = db_path or os.getenv("ASH_SESSION_DB", "").strip() or "var/db/agent-swarm-hub.sqlite3"
        self.db_path = Path(raw_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    session_key TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    thread_id TEXT,
                    active_task_id TEXT,
                    executor_session_id TEXT,
                    conversation_summary TEXT NOT NULL DEFAULT '',
                    swarm_state_json TEXT NOT NULL DEFAULT '',
                    escalations_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    session_key TEXT NOT NULL,
                    workspace_id TEXT NOT NULL DEFAULT 'default',
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    executor_session_id TEXT,
                    last_checkpoint TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    closed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_key TEXT NOT NULL,
                    task_id TEXT,
                    role TEXT NOT NULL,
                    platform_message_id TEXT,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_key TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    task_id TEXT,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS task_handoffs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_key TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    handoff_type TEXT NOT NULL,
                    source_agent TEXT NOT NULL,
                    target_agent TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ephemeral_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_key TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS workspaces (
                    workspace_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    path TEXT NOT NULL,
                    backend TEXT NOT NULL,
                    transport TEXT NOT NULL DEFAULT 'auto',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS chat_bindings (
                    session_key TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    thread_id TEXT,
                    workspace_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS workspace_sessions (
                    session_key TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    active_task_id TEXT,
                    executor_session_id TEXT,
                    claude_session_id TEXT,
                    codex_session_id TEXT,
                    phase TEXT NOT NULL DEFAULT 'discussion',
                    conversation_summary TEXT NOT NULL DEFAULT '',
                    swarm_state_json TEXT NOT NULL DEFAULT '',
                    escalations_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (session_key, workspace_id)
                );
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
            if "workspace_id" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN workspace_id TEXT NOT NULL DEFAULT 'default'")
            workspace_columns = {row["name"] for row in conn.execute("PRAGMA table_info(workspaces)").fetchall()}
            if "transport" not in workspace_columns:
                conn.execute("ALTER TABLE workspaces ADD COLUMN transport TEXT NOT NULL DEFAULT 'auto'")
            workspace_session_columns = {row["name"] for row in conn.execute("PRAGMA table_info(workspace_sessions)").fetchall()}
            if "claude_session_id" not in workspace_session_columns:
                conn.execute("ALTER TABLE workspace_sessions ADD COLUMN claude_session_id TEXT")
            if "codex_session_id" not in workspace_session_columns:
                conn.execute("ALTER TABLE workspace_sessions ADD COLUMN codex_session_id TEXT")
            if "phase" not in workspace_session_columns:
                conn.execute("ALTER TABLE workspace_sessions ADD COLUMN phase TEXT NOT NULL DEFAULT 'discussion'")

    def get_workspace(self, workspace_id: str) -> WorkspaceRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT workspace_id, title, path, backend, transport, created_at, updated_at
                FROM workspaces
                WHERE workspace_id = ?
                """,
                (workspace_id,),
            ).fetchone()
        if row is None:
            return None
        return WorkspaceRecord(**dict(row))

    def clear_chat_binding(self, session_key: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM chat_bindings WHERE session_key = ?", (session_key,))

    def list_workspaces(self) -> list[WorkspaceRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT workspace_id, title, path, backend, transport, created_at, updated_at
                FROM workspaces
                ORDER BY workspace_id ASC
                """
            ).fetchall()
        return [WorkspaceRecord(**dict(row)) for row in rows]

    def upsert_workspace(self, *, workspace_id: str, title: str, path: str, backend: str, transport: str = "auto") -> None:
        now = _utc_now()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM workspaces WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO workspaces (workspace_id, title, path, backend, transport, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id) DO UPDATE SET
                    title=excluded.title,
                    path=excluded.path,
                    backend=excluded.backend,
                    transport=excluded.transport,
                    updated_at=excluded.updated_at
                """,
                (workspace_id, title, path, backend, transport, created_at, now),
            )

    def get_chat_binding(self, session_key: str) -> ChatBindingRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT session_key, platform, chat_id, thread_id, workspace_id, created_at, updated_at
                FROM chat_bindings
                WHERE session_key = ?
                """,
                (session_key,),
            ).fetchone()
        if row is None:
            return None
        return ChatBindingRecord(**dict(row))

    def bind_chat(
        self,
        *,
        session_key: str,
        platform: str,
        chat_id: str,
        thread_id: str | None,
        workspace_id: str,
    ) -> None:
        now = _utc_now()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM chat_bindings WHERE session_key = ?",
                (session_key,),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO chat_bindings (session_key, platform, chat_id, thread_id, workspace_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_key) DO UPDATE SET
                    platform=excluded.platform,
                    chat_id=excluded.chat_id,
                    thread_id=excluded.thread_id,
                    workspace_id=excluded.workspace_id,
                    updated_at=excluded.updated_at
                """,
                (session_key, platform, chat_id, thread_id, workspace_id, created_at, now),
            )

    def get_workspace_session(self, session_key: str, workspace_id: str) -> WorkspaceSessionRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT session_key, workspace_id, active_task_id, executor_session_id, claude_session_id, codex_session_id, phase,
                       conversation_summary, swarm_state_json, escalations_json, created_at, updated_at
                FROM workspace_sessions
                WHERE session_key = ? AND workspace_id = ?
                """,
                (session_key, workspace_id),
            ).fetchone()
        if row is None:
            return None
        return WorkspaceSessionRecord(**dict(row))

    def upsert_workspace_session(
        self,
        *,
        session_key: str,
        workspace_id: str,
        active_task_id: str | None,
        executor_session_id: str | None,
        claude_session_id: str | None,
        codex_session_id: str | None,
        phase: str,
        conversation_summary: str,
        swarm_state_json: str,
        escalations_json: str,
    ) -> None:
        now = _utc_now()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM workspace_sessions WHERE session_key = ? AND workspace_id = ?",
                (session_key, workspace_id),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO workspace_sessions (
                    session_key, workspace_id, active_task_id, executor_session_id, claude_session_id, codex_session_id, phase,
                    conversation_summary, swarm_state_json, escalations_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_key, workspace_id) DO UPDATE SET
                    active_task_id=excluded.active_task_id,
                    executor_session_id=excluded.executor_session_id,
                    claude_session_id=excluded.claude_session_id,
                    codex_session_id=excluded.codex_session_id,
                    phase=excluded.phase,
                    conversation_summary=excluded.conversation_summary,
                    swarm_state_json=excluded.swarm_state_json,
                    escalations_json=excluded.escalations_json,
                    updated_at=excluded.updated_at
                """,
                (
                    session_key,
                    workspace_id,
                    active_task_id,
                    executor_session_id,
                    claude_session_id,
                    codex_session_id,
                    phase,
                    conversation_summary,
                    swarm_state_json,
                    escalations_json,
                    created_at,
                    now,
                ),
            )

    def clear_workspace_session(self, session_key: str, workspace_id: str) -> None:
        self.upsert_workspace_session(
            session_key=session_key,
            workspace_id=workspace_id,
            active_task_id=None,
            executor_session_id=None,
            claude_session_id=None,
            codex_session_id=None,
            phase="discussion",
            conversation_summary="No active task in this workspace yet. Use /write <task> first.",
            swarm_state_json="",
            escalations_json="[]",
        )

    def remove_workspace(self, workspace_id: str) -> None:
        if not workspace_id:
            return
        with self._connect() as conn:
            task_ids = [
                str(row["task_id"])
                for row in conn.execute(
                    "SELECT task_id FROM tasks WHERE workspace_id = ?",
                    (workspace_id,),
                ).fetchall()
                if (row["task_id"] or "")
            ]
            session_keys = [
                str(row["session_key"])
                for row in conn.execute(
                    "SELECT DISTINCT session_key FROM workspace_sessions WHERE workspace_id = ?",
                    (workspace_id,),
                ).fetchall()
                if (row["session_key"] or "")
            ]
            conn.execute("DELETE FROM task_handoffs WHERE workspace_id = ?", (workspace_id,))
            conn.execute("DELETE FROM agent_messages WHERE workspace_id = ?", (workspace_id,))
            conn.execute("DELETE FROM ephemeral_messages WHERE workspace_id = ?", (workspace_id,))
            conn.execute("DELETE FROM workspace_sessions WHERE workspace_id = ?", (workspace_id,))
            conn.execute("DELETE FROM tasks WHERE workspace_id = ?", (workspace_id,))
            conn.execute("DELETE FROM workspaces WHERE workspace_id = ?", (workspace_id,))
            conn.execute("DELETE FROM chat_bindings WHERE workspace_id = ?", (workspace_id,))
            if task_ids:
                placeholders = ",".join("?" for _ in task_ids)
                conn.execute(f"DELETE FROM messages WHERE task_id IN ({placeholders})", task_ids)
            if session_keys:
                placeholders = ",".join("?" for _ in session_keys)
                conn.execute(
                    f"""
                    DELETE FROM chat_sessions
                    WHERE session_key IN ({placeholders})
                      AND session_key NOT IN (SELECT session_key FROM chat_bindings)
                    """,
                    session_keys,
                )

    def get_session(self, session_key: str) -> ChatSessionRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT session_key, platform, chat_id, thread_id, active_task_id, executor_session_id,
                       conversation_summary, swarm_state_json, escalations_json, created_at, updated_at
                FROM chat_sessions
                WHERE session_key = ?
                """,
                (session_key,),
            ).fetchone()
        if row is None:
            return None
        return ChatSessionRecord(**dict(row))

    def upsert_session(
        self,
        *,
        session_key: str,
        platform: str,
        chat_id: str,
        thread_id: str | None,
        active_task_id: str | None,
        executor_session_id: str | None,
        conversation_summary: str,
        swarm_state_json: str,
        escalations_json: str,
    ) -> None:
        now = _utc_now()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM chat_sessions WHERE session_key = ?",
                (session_key,),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO chat_sessions (
                    session_key, platform, chat_id, thread_id, active_task_id, executor_session_id,
                    conversation_summary, swarm_state_json, escalations_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_key) DO UPDATE SET
                    platform=excluded.platform,
                    chat_id=excluded.chat_id,
                    thread_id=excluded.thread_id,
                    active_task_id=excluded.active_task_id,
                    executor_session_id=excluded.executor_session_id,
                    conversation_summary=excluded.conversation_summary,
                    swarm_state_json=excluded.swarm_state_json,
                    escalations_json=excluded.escalations_json,
                    updated_at=excluded.updated_at
                """,
                (
                    session_key,
                    platform,
                    chat_id,
                    thread_id,
                    active_task_id,
                    executor_session_id,
                    conversation_summary,
                    swarm_state_json,
                    escalations_json,
                    created_at,
                    now,
                ),
            )

    def upsert_task(
        self,
        *,
        task_id: str,
        session_key: str,
        workspace_id: str,
        title: str,
        status: str,
        executor_session_id: str | None = None,
        last_checkpoint: str = "",
        closed_at: str | None = None,
    ) -> None:
        now = _utc_now()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            conn.execute(
                """
                INSERT INTO tasks (
                    task_id, session_key, workspace_id, title, status, executor_session_id, last_checkpoint,
                    created_at, updated_at, closed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    session_key=excluded.session_key,
                    workspace_id=excluded.workspace_id,
                    title=excluded.title,
                    status=excluded.status,
                    executor_session_id=excluded.executor_session_id,
                    last_checkpoint=excluded.last_checkpoint,
                    updated_at=excluded.updated_at,
                    closed_at=excluded.closed_at
                """,
                (
                    task_id,
                    session_key,
                    workspace_id,
                    title,
                    status,
                    executor_session_id,
                    last_checkpoint,
                    created_at,
                    now,
                    closed_at,
                ),
            )

    def append_message(
        self,
        *,
        session_key: str,
        role: str,
        text: str,
        task_id: str | None = None,
        platform_message_id: str | None = None,
    ) -> None:
        normalized_session_key = str(session_key)
        normalized_task_id = None if task_id is None else str(task_id)
        normalized_role = str(role)
        normalized_platform_message_id = "" if platform_message_id is None else str(platform_message_id)
        normalized_text = str(text)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO messages (session_key, task_id, role, platform_message_id, text, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_session_key,
                    normalized_task_id,
                    normalized_role,
                    normalized_platform_message_id,
                    normalized_text,
                    _utc_now(),
                ),
            )

    def append_agent_message(
        self,
        *,
        session_key: str,
        workspace_id: str,
        agent: str,
        role: str,
        text: str,
        task_id: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_messages (session_key, workspace_id, agent, task_id, role, text, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_key, workspace_id, agent, task_id, role, text, _utc_now()),
            )

    def list_recent_messages(self, session_key: str, *, limit: int = 20) -> list[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT session_key, task_id, role, platform_message_id, text, created_at
                FROM messages
                WHERE session_key = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_key, limit),
            ).fetchall()
        return list(reversed(rows))

    def list_recent_agent_messages(
        self,
        session_key: str,
        workspace_id: str,
        agent: str,
        *,
        limit: int = 12,
    ) -> list[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT session_key, workspace_id, agent, task_id, role, text, created_at
                FROM agent_messages
                WHERE session_key = ? AND workspace_id = ? AND agent = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_key, workspace_id, agent, limit),
            ).fetchall()
        return list(reversed(rows))

    def list_tasks(self, session_key: str, workspace_id: str, *, limit: int = 20) -> list[TaskRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT task_id, session_key, workspace_id, title, status, executor_session_id, last_checkpoint,
                       created_at, updated_at, closed_at
                FROM tasks
                WHERE session_key = ? AND workspace_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (session_key, workspace_id, limit),
            ).fetchall()
        return [TaskRecord(**dict(row)) for row in rows]

    def append_task_handoff(
        self,
        *,
        session_key: str,
        workspace_id: str,
        task_id: str,
        handoff_type: str,
        source_agent: str,
        target_agent: str,
        content_json: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO task_handoffs (
                    session_key, workspace_id, task_id, handoff_type, source_agent, target_agent, content_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_key, workspace_id, task_id, handoff_type, source_agent, target_agent, content_json, _utc_now()),
            )

    def list_task_handoffs(
        self,
        session_key: str,
        workspace_id: str,
        task_id: str,
        *,
        limit: int = 10,
    ) -> list[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT session_key, workspace_id, task_id, handoff_type, source_agent, target_agent, content_json, created_at
                FROM task_handoffs
                WHERE session_key = ? AND workspace_id = ? AND task_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_key, workspace_id, task_id, limit),
            ).fetchall()
        return list(reversed(rows))

    def append_ephemeral_message(
        self,
        *,
        session_key: str,
        workspace_id: str,
        agent: str,
        role: str,
        text: str,
        expires_at: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ephemeral_messages (session_key, workspace_id, agent, role, text, expires_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_key, workspace_id, agent, role, text, expires_at, _utc_now()),
            )

    def purge_expired_ephemeral_messages(self) -> None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute("DELETE FROM ephemeral_messages WHERE expires_at <= ?", (now,))

    def trim_ephemeral_messages(self, session_key: str, workspace_id: str, agent: str, *, keep: int = 5) -> None:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id
                FROM ephemeral_messages
                WHERE session_key = ? AND workspace_id = ? AND agent = ?
                ORDER BY id DESC
                """,
                (session_key, workspace_id, agent),
            ).fetchall()
            stale_ids = [row["id"] for row in rows[keep:]]
            if stale_ids:
                conn.executemany("DELETE FROM ephemeral_messages WHERE id = ?", [(item,) for item in stale_ids])

    def list_ephemeral_messages(
        self,
        session_key: str,
        workspace_id: str,
        agent: str,
        *,
        limit: int = 5,
    ) -> list[sqlite3.Row]:
        self.purge_expired_ephemeral_messages()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT session_key, workspace_id, agent, role, text, expires_at, created_at
                FROM ephemeral_messages
                WHERE session_key = ? AND workspace_id = ? AND agent = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_key, workspace_id, agent, limit),
            ).fetchall()
        return list(reversed(rows))

    def clear_ephemeral_messages(self, session_key: str, workspace_id: str | None = None) -> None:
        with self._connect() as conn:
            if workspace_id is None:
                conn.execute("DELETE FROM ephemeral_messages WHERE session_key = ?", (session_key,))
            else:
                conn.execute(
                    "DELETE FROM ephemeral_messages WHERE session_key = ? AND workspace_id = ?",
                    (session_key, workspace_id),
                )

    @staticmethod
    def dumps_json(data: object) -> str:
        return json.dumps(data, ensure_ascii=False)
