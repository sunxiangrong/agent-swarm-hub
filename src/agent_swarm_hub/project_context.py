from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from hashlib import sha1
from pathlib import Path
from typing import Any

from .executor import Executor, build_executor_for_config
from .openviking_support import read_openviking_overview
from .paths import project_session_db_path


_PROMPT_PROFILE_LIMIT = 160
_PROMPT_FIELD_LIMIT = 180
_PROMPT_MESSAGE_LIMIT = 120
_PROMPT_RECENT_MESSAGE_COUNT = 2
_GLOBAL_MEMORY_LIMIT = 8
_GLOBAL_MEMORY_FILE = "SHARED_MEMORY.md"
_ALL_PROJECTS_SCOPE = "shared:all-projects"
_BIOINFO_SCOPE = "shared:bioinfo"
_KNOWLEDGE_SYSTEM_PROJECT = "knowledge-system"
_WORKBENCH_PROJECT = "ash-workbench"


def project_ov_resource_uri(project_id: str) -> str:
    return f"viking://resources/projects/{project_id}"


@dataclass(frozen=True, slots=True)
class ProjectContext:
    project_id: str
    title: str
    workspace_path: str
    profile: str
    summary: str
    provider_session_count: int
    active_session_count: int = 0
    binding_claude_session_id: str = ""
    binding_codex_session_id: str = ""
    recent_messages: tuple[str, ...] = ()


class ProjectContextStore:
    def __init__(self, db_path: str | None = None):
        self.db_path = Path(db_path).expanduser() if db_path else project_session_db_path()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS projects (
                        project_id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        workspace_path TEXT NOT NULL DEFAULT '',
                        profile TEXT NOT NULL DEFAULT '',
                        summary TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );

                    CREATE TABLE IF NOT EXISTS provider_sessions (
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

                    CREATE TABLE IF NOT EXISTS project_memory (
                        project_id TEXT PRIMARY KEY,
                        focus TEXT NOT NULL DEFAULT '',
                        current_state TEXT NOT NULL DEFAULT '',
                        recent_context TEXT NOT NULL DEFAULT '',
                        memory TEXT NOT NULL DEFAULT '',
                        recent_hints_json TEXT NOT NULL DEFAULT '[]',
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );

                    CREATE TABLE IF NOT EXISTS provider_bindings (
                        project_id TEXT NOT NULL,
                        provider TEXT NOT NULL,
                        raw_session_id TEXT NOT NULL DEFAULT '',
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (project_id, provider)
                    );

                    CREATE TABLE IF NOT EXISTS project_sessions (
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

                    CREATE INDEX IF NOT EXISTS idx_project_sessions_project
                    ON project_sessions(project_id, provider, status, last_used_at DESC);

                    CREATE TABLE IF NOT EXISTS dashboard_project_pins (
                        project_id TEXT PRIMARY KEY,
                        pinned INTEGER NOT NULL DEFAULT 1,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );

                    CREATE TABLE IF NOT EXISTS global_memory (
                        memory_key TEXT PRIMARY KEY,
                        scope TEXT NOT NULL DEFAULT 'global',
                        category TEXT NOT NULL DEFAULT 'workflow',
                        content TEXT NOT NULL DEFAULT '',
                        source_project_id TEXT NOT NULL DEFAULT '',
                        confidence REAL NOT NULL DEFAULT 0.5,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    );

                    CREATE TABLE IF NOT EXISTS project_memory_scopes (
                        project_id TEXT NOT NULL,
                        scope TEXT NOT NULL,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (project_id, scope)
                    );

                    CREATE TABLE IF NOT EXISTS project_runtime_health (
                        project_id TEXT NOT NULL,
                        provider TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT '',
                        summary TEXT NOT NULL DEFAULT '',
                        details_json TEXT NOT NULL DEFAULT '{}',
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (project_id, provider)
                    );

                    CREATE TABLE IF NOT EXISTS project_auto_continue_state (
                        project_id TEXT NOT NULL,
                        provider TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT '',
                        summary TEXT NOT NULL DEFAULT '',
                        details_json TEXT NOT NULL DEFAULT '{}',
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (project_id, provider)
                    );
                    """
                )
                self._migrate_project_memory_schema(conn)
            except sqlite3.OperationalError as exc:
                if "readonly" not in str(exc).lower():
                    raise

    def _migrate_project_memory_schema(self, conn: sqlite3.Connection) -> None:
        try:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(project_memory)").fetchall()}
        except sqlite3.Error:
            return
        try:
            if "current_state" not in columns:
                conn.execute("ALTER TABLE project_memory ADD COLUMN current_state TEXT NOT NULL DEFAULT ''")
                conn.execute(
                    """
                    UPDATE project_memory
                    SET current_state = COALESCE(NULLIF(recent_context, ''), current_state, '')
                    WHERE COALESCE(current_state, '') = ''
                    """
                )
            if "recent_context" not in columns:
                conn.execute("ALTER TABLE project_memory ADD COLUMN recent_context TEXT NOT NULL DEFAULT ''")
                conn.execute(
                    """
                    UPDATE project_memory
                    SET recent_context = COALESCE(NULLIF(current_state, ''), recent_context, '')
                    WHERE COALESCE(recent_context, '') = ''
                    """
                )
        except sqlite3.Error:
            return
        try:
            global_columns = {row["name"] for row in conn.execute("PRAGMA table_info(global_memory)").fetchall()}
            if "scope" not in global_columns:
                conn.execute("ALTER TABLE global_memory ADD COLUMN scope TEXT NOT NULL DEFAULT 'global'")
                conn.execute("UPDATE global_memory SET scope = 'global' WHERE COALESCE(scope, '') = ''")
        except sqlite3.Error:
            return

    def list_projects(self) -> list[ProjectContext]:
        if not self.db_path.exists():
            return []
        with self._connect() as conn:
            provider_columns = {row["name"] for row in conn.execute("PRAGMA table_info(provider_sessions)").fetchall()}
            project_columns = {row["name"] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
            active_expr = "SUM(CASE WHEN ps.status = 'active' THEN 1 ELSE 0 END)" if "status" in provider_columns else "COUNT(ps.raw_session_id)"
            profile_expr = "p.profile" if "profile" in project_columns else "'' AS profile"
            rows = conn.execute(
                f"""
                SELECT p.project_id, p.title, p.workspace_path, {profile_expr}, p.summary,
                       COUNT(DISTINCT ps.provider || ':' || ps.raw_session_id) AS provider_session_count,
                       {active_expr} AS active_session_count,
                       COALESCE(MAX(CASE WHEN pb.provider = 'claude' THEN pb.raw_session_id END), '') AS binding_claude_session_id,
                       COALESCE(MAX(CASE WHEN pb.provider = 'codex' THEN pb.raw_session_id END), '') AS binding_codex_session_id
                FROM projects p
                LEFT JOIN provider_sessions ps ON ps.project_id = p.project_id
                LEFT JOIN provider_bindings pb ON pb.project_id = p.project_id
                GROUP BY p.project_id, p.title, p.workspace_path, profile, p.summary
                ORDER BY p.project_id ASC
                """
            ).fetchall()
        projects: list[ProjectContext] = []
        for row in rows:
            payload = dict(row)
            payload["active_session_count"] = payload.get("active_session_count") or 0
            payload["recent_messages"] = ()
            projects.append(ProjectContext(**payload))
        return projects

    def get_project(self, project_id: str) -> ProjectContext | None:
        if not project_id or not self.db_path.exists():
            return None
        for project in self.list_projects():
            if project.project_id == project_id:
                return project
        return None

    def list_pinned_projects(self) -> set[str]:
        if not self.db_path.exists():
            return set()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT project_id
                FROM dashboard_project_pins
                WHERE pinned = 1
                """
            ).fetchall()
        return {str(row["project_id"]).strip() for row in rows if (row["project_id"] or "").strip()}

    def set_project_pinned(self, project_id: str, pinned: bool) -> None:
        if not project_id:
            return
        try:
            with self._connect() as conn:
                if pinned:
                    conn.execute(
                        """
                        INSERT INTO dashboard_project_pins (project_id, pinned, updated_at)
                        VALUES (?, 1, CURRENT_TIMESTAMP)
                        ON CONFLICT(project_id) DO UPDATE SET
                            pinned = 1,
                            updated_at = CURRENT_TIMESTAMP
                        """,
                        (project_id,),
                    )
                else:
                    conn.execute("DELETE FROM dashboard_project_pins WHERE project_id = ?", (project_id,))
        except sqlite3.Error:
            return

    def remove_project(self, project_id: str) -> None:
        if not project_id:
            return
        try:
            with self._connect() as conn:
                conn.execute("DELETE FROM provider_bindings WHERE project_id = ?", (project_id,))
                conn.execute("DELETE FROM project_memory WHERE project_id = ?", (project_id,))
                conn.execute("DELETE FROM project_sessions WHERE project_id = ?", (project_id,))
                conn.execute("DELETE FROM provider_sessions WHERE project_id = ?", (project_id,))
                conn.execute("DELETE FROM dashboard_project_pins WHERE project_id = ?", (project_id,))
                conn.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))
        except sqlite3.Error:
            return

    def get_for_workspace_path(self, workspace_path: str | None) -> ProjectContext | None:
        if not workspace_path or not self.db_path.exists():
            return None
        resolved = str(Path(workspace_path).expanduser().resolve())
        with self._connect() as conn:
            provider_columns = {row["name"] for row in conn.execute("PRAGMA table_info(provider_sessions)").fetchall()}
            project_columns = {row["name"] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
            tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            active_expr = "SUM(CASE WHEN ps.status = 'active' THEN 1 ELSE 0 END)" if "status" in provider_columns else "COUNT(ps.raw_session_id)"
            profile_expr = "p.profile" if "profile" in project_columns else "'' AS profile"
            row = conn.execute(
                f"""
                SELECT p.project_id, p.title, p.workspace_path, {profile_expr}, p.summary,
                       COUNT(DISTINCT ps.provider || ':' || ps.raw_session_id) AS provider_session_count,
                       {active_expr} AS active_session_count,
                       COALESCE(MAX(CASE WHEN pb.provider = 'claude' THEN pb.raw_session_id END), '') AS binding_claude_session_id,
                       COALESCE(MAX(CASE WHEN pb.provider = 'codex' THEN pb.raw_session_id END), '') AS binding_codex_session_id
                FROM projects p
                LEFT JOIN provider_sessions ps ON ps.project_id = p.project_id
                LEFT JOIN provider_bindings pb ON pb.project_id = p.project_id
                WHERE p.workspace_path = ?
                GROUP BY p.project_id, p.title, p.workspace_path, profile, p.summary
                """,
                (resolved,),
            ).fetchone()
            recent_rows = []
            if row is not None and "project_messages" in tables:
                recent_rows = conn.execute(
                    """
                    SELECT role, text
                    FROM project_messages
                    WHERE project_id = ?
                    ORDER BY id DESC
                    LIMIT 6
                    """,
                    (row["project_id"],),
                ).fetchall()
        if row is None:
            return None
        recent_messages = tuple(
            f"{item['role']}: {item['text'][:240].strip()}"
            for item in reversed(recent_rows)
            if (item["text"] or "").strip()
        )
        payload = dict(row)
        payload["active_session_count"] = payload.get("active_session_count") or 0
        payload["recent_messages"] = recent_messages
        return ProjectContext(**payload)

    def build_prompt_context(self, workspace_path: str | None) -> str:
        project = self.get_for_workspace_path(workspace_path)
        if project is None:
            return ""
        snapshot = self.build_memory_snapshot(workspace_path)
        lines = [
            f"Project: {project.project_id}",
            f"Workspace: {snapshot['workspace']}",
            f"Profile: {snapshot['profile']}",
            f"Active Provider Sessions: {project.active_session_count}",
        ]
        if snapshot["focus"]:
            lines.append(f"Focus: {snapshot['focus']}")
        if snapshot.get("current_phase"):
            lines.append(f"Current Phase: {snapshot['current_phase']}")
        if snapshot["recent_context"]:
            lines.append(f"Current State: {snapshot['recent_context']}")
        if snapshot.get("current_blocker"):
            lines.append(f"Current Blocker: {snapshot['current_blocker']}")
        if snapshot.get("last_verified_result"):
            lines.append(f"Last Verified Result: {snapshot['last_verified_result']}")
        if snapshot.get("runtime_health_summary"):
            lines.append(f"Runtime Health: {snapshot['runtime_health_summary']}")
        if snapshot.get("auto_continue_summary"):
            lines.append(f"Auto-continue: {snapshot['auto_continue_summary']}")
        elif snapshot["memory"]:
            lines.append(f"Cache Summary: {snapshot['memory']}")
        if snapshot["recent_hints"]:
            lines.append("Recent Memory Hints:")
            lines.extend(f"- {message}" for message in snapshot["recent_hints"])
        if snapshot["global_memory"]:
            lines.append(f"Global Memory: {snapshot['global_memory']}")
        if snapshot.get("shared_scopes"):
            lines.append(f"Shared Scopes: {', '.join(snapshot['shared_scopes'])}")
        return "\n".join(lines)

    def build_memory_snapshot(self, workspace_path: str | None) -> dict[str, Any]:
        project = self.get_for_workspace_path(workspace_path)
        global_snapshot = self.build_global_memory_snapshot()
        if project is None:
            return self._empty_memory_snapshot(global_snapshot)
        stored_memory = self.get_project_memory(project.project_id)
        shared_scopes = self.resolve_project_memory_scopes(project.project_id)
        shared_snapshot = self.build_global_memory_snapshot(scopes=shared_scopes, include_global=False)
        combined_snapshot = self.build_global_memory_snapshot(scopes=shared_scopes, include_global=True)
        runtime_health = self.get_runtime_health(project.project_id, "codex")
        auto_continue_state = self.get_auto_continue_state(project.project_id, "codex")
        focus, recent_context, memory, recent_messages = self._project_memory_values(project, stored_memory)
        daily_projection = self.derive_daily_projection(
            project_id=project.project_id,
            focus=focus,
            current_state=recent_context,
            memory=memory,
            hints=recent_messages,
            workspace_path=project.workspace_path or "",
        )
        return {
            "project_id": project.project_id,
            "workspace": self._compact(project.workspace_path, _PROMPT_FIELD_LIMIT),
            "profile": self._compact(project.profile, _PROMPT_PROFILE_LIMIT),
            "focus": self._compact(focus, _PROMPT_FIELD_LIMIT) if focus else "",
            "current_state": self._compact(recent_context, _PROMPT_FIELD_LIMIT) if recent_context else "",
            "recent_context": self._compact(recent_context, _PROMPT_FIELD_LIMIT) if recent_context else "",
            "memory": self._compact(memory, _PROMPT_FIELD_LIMIT) if memory else "",
            "recent_hints": recent_messages,
            "current_phase": daily_projection["current_phase"],
            "current_blocker": daily_projection["current_blocker"],
            "last_verified_result": daily_projection["last_verified_result"],
            "stable_memory_hint": daily_projection["stable_memory_hint"],
            "daily_state_source": daily_projection["state_source"],
            "runtime_health_status": str(runtime_health.get("status") or ""),
            "runtime_health_summary": self._compact(str(runtime_health.get("summary") or ""), 220) if runtime_health.get("summary") else "",
            "runtime_health_updated_at": str(runtime_health.get("updated_at") or ""),
            "runtime_health_details": dict(runtime_health.get("details") or {}),
            "auto_continue_status": str(auto_continue_state.get("status") or ""),
            "auto_continue_summary": self._compact(str(auto_continue_state.get("summary") or ""), 220) if auto_continue_state.get("summary") else "",
            "auto_continue_updated_at": str(auto_continue_state.get("updated_at") or ""),
            "auto_continue_details": dict(auto_continue_state.get("details") or {}),
            "global_memory": combined_snapshot["summary"],
            "global_hints": combined_snapshot["hints"],
            "shared_memory": shared_snapshot["summary"],
            "shared_hints": shared_snapshot["hints"],
            "shared_scopes": shared_scopes,
            "universal_memory": global_snapshot["summary"],
            "universal_hints": global_snapshot["hints"],
        }

    def list_global_memory(
        self,
        *,
        limit: int = _GLOBAL_MEMORY_LIMIT,
        scopes: list[str] | None = None,
        include_global: bool = True,
    ) -> list[dict[str, Any]]:
        if not self.db_path.exists():
            return []
        normalized_scopes = [self._normalize_memory_scope(item) for item in (scopes or []) if self._normalize_memory_scope(item)]
        clauses: list[str] = []
        params: list[Any] = []
        if normalized_scopes:
            placeholders = ",".join("?" for _ in normalized_scopes)
            clauses.append(f"scope IN ({placeholders})")
            params.extend(normalized_scopes)
        if include_global:
            clauses.append("scope = 'global'")
        if not clauses:
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    f"""
                    SELECT memory_key, scope, category, content, source_project_id, confidence, updated_at
                    FROM global_memory
                    WHERE COALESCE(content, '') != ''
                      AND ({' OR '.join(clauses)})
                    ORDER BY confidence DESC, updated_at DESC, memory_key ASC
                    LIMIT ?
                    """,
                    (*params, max(1, int(limit))),
                ).fetchall()
        except sqlite3.Error:
            return []
        return [dict(row) for row in rows]

    def build_global_memory_snapshot(
        self,
        *,
        scopes: list[str] | None = None,
        include_global: bool = True,
    ) -> dict[str, Any]:
        rows = self.list_global_memory(scopes=scopes, include_global=include_global)
        hints = [self._compact(str(row.get("content") or ""), _PROMPT_MESSAGE_LIMIT) for row in rows if str(row.get("content") or "").strip()]
        summary = self._compact(" | ".join(hints[:3]), 220) if hints else ""
        return {"summary": summary, "hints": hints[:_PROMPT_RECENT_MESSAGE_COUNT]}

    def upsert_global_memory(
        self,
        *,
        content: str,
        scope: str = "global",
        category: str = "workflow",
        source_project_id: str = "",
        confidence: float = 0.6,
    ) -> bool:
        normalized = self._compact(self._strip_memory_label(content), 280)
        if not normalized:
            return False
        scope_value = self._normalize_memory_scope(scope) or "global"
        memory_key = self._scoped_memory_key(scope_value, normalized)
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO global_memory (memory_key, scope, category, content, source_project_id, confidence, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(memory_key) DO UPDATE SET
                        scope = excluded.scope,
                        category = excluded.category,
                        content = excluded.content,
                        source_project_id = CASE
                            WHEN excluded.source_project_id != '' THEN excluded.source_project_id
                            ELSE global_memory.source_project_id
                        END,
                        confidence = CASE
                            WHEN excluded.confidence > global_memory.confidence THEN excluded.confidence
                            ELSE global_memory.confidence
                        END,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (memory_key, scope_value, category.strip() or "workflow", normalized, source_project_id.strip(), float(confidence)),
                )
        except sqlite3.Error:
            return False
        return True

    def list_project_memory_scopes(self, project_id: str) -> list[str]:
        if not project_id or not self.db_path.exists():
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT scope
                    FROM project_memory_scopes
                    WHERE project_id = ?
                    ORDER BY scope ASC
                    """,
                    (project_id,),
                ).fetchall()
        except sqlite3.Error:
            return []
        return [str(row["scope"]).strip() for row in rows if str(row["scope"]).strip()]

    def resolve_project_memory_scopes(self, project_id: str) -> list[str]:
        scopes = set(self.list_project_memory_scopes(project_id))
        scopes.update(self.default_project_memory_scopes(project_id))
        if project_id == _KNOWLEDGE_SYSTEM_PROJECT:
            scopes.update(self.list_memory_scopes())
        return sorted(scope for scope in scopes if scope and scope != "global")

    def default_project_memory_scopes(self, project_id: str) -> list[str]:
        normalized = (project_id or "").strip().casefold()
        if not normalized:
            return []
        scopes = {_ALL_PROJECTS_SCOPE}
        bioinfo_markers = ("gwas", "qtl", "genome", "scpagwas", "bioinfo")
        if normalized in {"cell_qtl", "genome_functional", "post-gwas", "scpagwas_celltype"} or any(
            marker in normalized for marker in bioinfo_markers
        ):
            scopes.add(_BIOINFO_SCOPE)
        return sorted(scopes)

    def ensure_default_project_memory_scopes(self, project_id: str) -> int:
        added = 0
        for scope in self.default_project_memory_scopes(project_id):
            if self.bind_project_memory_scope(project_id, scope):
                added += 1
        return added

    def ensure_default_memory_scopes_for_all_projects(self) -> int:
        added = 0
        for project in self.list_projects():
            added += self.ensure_default_project_memory_scopes(project.project_id)
        return added

    def bind_project_memory_scope(self, project_id: str, scope: str) -> bool:
        normalized = self._normalize_memory_scope(scope)
        if not project_id or not normalized or normalized == "global":
            return False
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO project_memory_scopes (project_id, scope, updated_at)
                    VALUES (?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(project_id, scope) DO UPDATE SET
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (project_id, normalized),
                )
        except sqlite3.Error:
            return False
        return True

    def unbind_project_memory_scope(self, project_id: str, scope: str) -> bool:
        normalized = self._normalize_memory_scope(scope)
        if not project_id or not normalized:
            return False
        try:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM project_memory_scopes WHERE project_id = ? AND scope = ?",
                    (project_id, normalized),
                )
        except sqlite3.Error:
            return False
        return True

    def prune_global_memory(self) -> int:
        rows = self.list_global_memory(limit=256)
        stale_keys = [
            str(row.get("memory_key") or "")
            for row in rows
            if not self._accept_global_memory_candidate(
                str(row.get("source_project_id") or ""),
                str(row.get("content") or ""),
                from_ai=False,
            )
        ]
        if not stale_keys:
            return 0
        try:
            with self._connect() as conn:
                conn.executemany(
                    "DELETE FROM global_memory WHERE memory_key = ?",
                    [(key,) for key in stale_keys if key],
                )
        except sqlite3.Error:
            return 0
        return len(stale_keys)

    def promote_project_memory_to_global(
        self,
        project_id: str,
        *,
        focus: str = "",
        recent_context: str = "",
        memory: str = "",
        recent_hints: list[str] | None = None,
        ai_candidates: list[dict[str, Any]] | None = None,
    ) -> int:
        promoted = 0
        mode = self._global_memory_promotion_mode()
        for candidate in self._global_memory_candidates(
            project_id,
            focus=focus,
            recent_context=recent_context,
            memory=memory,
            recent_hints=recent_hints or [],
            ai_candidates=ai_candidates or [],
            mode=mode,
        ):
            if self.upsert_global_memory(
                content=candidate["content"],
                category=candidate["category"],
                source_project_id=project_id,
                confidence=candidate["confidence"],
            ):
                promoted += 1
        return promoted

    @classmethod
    def derive_session_brief(
        cls,
        *,
        focus: str,
        recent_context: str,
        memory: str,
        hints: list[str] | None,
    ) -> dict[str, str]:
        normalized_focus = cls._compact(" ".join((focus or "").split()), 220) if focus else ""
        normalized_state = cls._compact(" ".join((recent_context or "").split()), 220) if recent_context else ""
        compact_memory = cls._compact(" ".join((memory or "").split()), 220) if memory else ""
        next_step = cls._derive_next_step(
            focus=normalized_focus,
            current_state=normalized_state,
            memory=compact_memory,
            hints=hints or [],
        )
        if cls._memory_values_match(next_step, normalized_focus) or cls._memory_values_match(next_step, normalized_state):
            next_step = ""
        memory_brief = compact_memory
        if memory_brief:
            memory_brief = cls._strip_memory_label(memory_brief)
            if cls._memory_values_match(memory_brief, normalized_focus) or cls._memory_values_match(memory_brief, normalized_state):
                memory_brief = ""
        return {
            "focus": normalized_focus,
            "current_state": normalized_state,
            "recent_context": normalized_state,
            "next_step": next_step,
            "memory": memory_brief,
        }

    @classmethod
    def _daily_projection_is_low_signal(cls, text: str) -> bool:
        lowered = " ".join((text or "").split()).strip().casefold()
        if not lowered:
            return True
        return any(
            marker in lowered
            for marker in (
                "user:",
                "assistant:",
                "project summary for this session",
                "hi",
                "hello",
                "继续",
                "好的",
                "看看任务进度",
                "查看链接恢复",
            )
        )

    @classmethod
    def _daily_projection_verified_result(cls, current_state: str, memory: str) -> str:
        for raw in (current_state, memory):
            value = cls._strip_memory_label(raw)
            lowered = value.casefold()
            if value and any(
                marker in lowered
                for marker in (
                    "verified",
                    "confirmed",
                    "validated",
                    "passed",
                    "through",
                    "done",
                    "completed",
                    "已完成",
                    "已实现",
                    "已通过",
                    "已验证",
                    "已确认",
                    "已接入",
                    "已加上",
                    "已固定",
                    "已锁定",
                    "is now fixed",
                    "now fixed",
                )
            ):
                return cls._compact(value, 120)
        return ""

    @classmethod
    def _daily_projection_clean_memory(cls, memory: str) -> str:
        text = " ".join((memory or "").split()).strip()
        for separator in (" | State:", "| State:", "| state:"):
            if separator in text:
                text = text.split(separator, 1)[0].strip()
        return cls._strip_memory_label(text)

    @classmethod
    def _daily_projection_blocker(cls, current_state: str, next_step: str) -> str:
        state = cls._strip_memory_label(current_state)
        lowered_state = state.casefold()
        if state and any(
            marker in lowered_state
            for marker in ("blocked", "blocker", "卡住", "阻塞", "等待", "missing", "尚未", "无法", "not found", "error", "failed")
        ):
            return cls._compact(state, 120)
        step = cls._strip_memory_label(next_step)
        lowered_step = step.casefold()
        if step and any(marker in lowered_step for marker in ("需要先", "before", "依赖", "等待", "确认")):
            return cls._compact(step, 120)
        return ""

    @classmethod
    def _daily_projection_phase(cls, focus: str, current_state: str, next_step: str, blocker: str) -> str:
        if blocker:
            return "blocked"
        merged = " ".join((focus, current_state, next_step)).casefold()
        phase_hints = {
            "implementation": ("implement", "hardening", "接入", "实现", "重构", "优化", "修复", "开发"),
            "validation": ("verify", "validated", "validation", "测试", "验证", "回归", "检查", "确认"),
            "consolidation": ("consolidating", "整理", "汇总", "归档", "梳理", "收口", "总结"),
            "monitoring": ("monitor", "monitoring", "heartbeat", "巡检", "排查", "health"),
            "design": ("design", "roadmap", "方案", "设计", "规划"),
        }
        for phase, markers in phase_hints.items():
            if any(marker in merged for marker in markers):
                return phase
        return "active"

    @classmethod
    def derive_daily_projection(
        cls,
        *,
        project_id: str,
        focus: str,
        current_state: str,
        memory: str,
        hints: list[str] | None,
        workspace_path: str = "",
    ) -> dict[str, str]:
        brief = cls.derive_session_brief(
            focus=focus,
            recent_context=current_state,
            memory=memory,
            hints=hints or [],
        )
        normalized_focus = brief["focus"]
        raw_state = " ".join((current_state or "").split()).strip()
        cleaned_state = cls._strip_memory_label(raw_state)
        next_step = cls._strip_memory_label(brief["next_step"])
        normalized_hints = [
            (str(item), cls._strip_memory_label(item))
            for item in (hints or [])
            if cls._strip_memory_label(item)
        ]
        if cls._daily_projection_is_low_signal(next_step):
            next_step = ""
        if cls._memory_values_match(next_step, normalized_focus) or cls._memory_values_match(next_step, cleaned_state):
            next_step = ""
        if next_step and any(
            cls._memory_values_match(next_step, cleaned_hint) and cls._daily_projection_is_low_signal(raw_hint)
            for raw_hint, cleaned_hint in normalized_hints
        ):
            next_step = ""
        cleaned_memory = cls._daily_projection_clean_memory(memory)
        blocker = cls._daily_projection_blocker(cleaned_state, next_step)
        phase = cls._daily_projection_phase(normalized_focus, cleaned_state, next_step, blocker)
        state_source = "empty"
        if cleaned_state and not cls._daily_projection_is_low_signal(raw_state):
            state_value = cls._compact(cleaned_state, 140)
            state_source = "recent_context"
        elif cleaned_memory and cls._daily_projection_verified_result("", cleaned_memory):
            state_value = cls._compact(cleaned_memory, 140)
            state_source = "memory"
        elif next_step:
            state_value = f"当前阶段正在收口下一步：{cls._compact(next_step, 100)}"
            state_source = "next_step"
        elif normalized_focus:
            prefix_map = {
                "validation": "当前处于核查阶段：",
                "consolidation": "当前处于整理收口阶段：",
                "monitoring": "当前处于巡检监控阶段：",
                "implementation": "当前处于实现推进阶段：",
                "design": "当前处于方案设计阶段：",
            }
            state_value = f"{prefix_map.get(phase, '当前围绕项目目标推进：')}{cls._compact(normalized_focus, 100)}"
            state_source = "focus"
        else:
            state_value = ""
        verified = cls._daily_projection_verified_result(cleaned_state, cleaned_memory)
        stable_hint = ""
        if cleaned_memory and not verified:
            candidate_hint = cls._compact(cleaned_memory, 110)
            if not cls._memory_values_match(candidate_hint, normalized_focus):
                stable_hint = candidate_hint
        return {
            "project_id": project_id,
            "workspace_path": workspace_path,
            "focus": cls._compact(normalized_focus, 120),
            "current_phase": phase,
            "current_state": state_value or "暂无稳定阶段状态",
            "current_blocker": blocker,
            "next_step": cls._compact(next_step, 120) if next_step else "",
            "last_verified_result": verified,
            "stable_memory_hint": stable_hint,
            "state_source": state_source,
        }

    def build_daily_projection(self, project_id: str) -> dict[str, str] | None:
        project = self.get_project(project_id)
        if project is None:
            return None
        stored_memory = self.get_project_memory(project_id)
        focus, current_state, memory, recent_hints = self._project_memory_values(project, stored_memory)
        if not any((focus, current_state, memory, recent_hints)):
            return None
        return self.derive_daily_projection(
            project_id=project_id,
            focus=focus,
            current_state=current_state,
            memory=memory,
            hints=recent_hints,
            workspace_path=project.workspace_path or "",
        )

    def get_project_memory(self, project_id: str) -> dict[str, Any]:
        if not project_id or not self.db_path.exists():
            return {"focus": "", "current_state": "", "recent_context": "", "memory": "", "recent_hints": []}
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT focus,
                       COALESCE(NULLIF(current_state, ''), recent_context, '') AS current_state,
                       COALESCE(NULLIF(current_state, ''), recent_context, '') AS recent_context,
                       memory,
                       recent_hints_json
                FROM project_memory
                WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()
        if row is None:
            return {"focus": "", "current_state": "", "recent_context": "", "memory": "", "recent_hints": []}
        current_state = self._compact(row["current_state"], _PROMPT_FIELD_LIMIT) if row["current_state"] else ""
        return {
            "focus": self._compact(row["focus"], _PROMPT_FIELD_LIMIT) if row["focus"] else "",
            "current_state": current_state,
            "recent_context": current_state,
            "memory": self._compact(row["memory"], _PROMPT_FIELD_LIMIT) if row["memory"] else "",
            "recent_hints": self._parse_hints_json(row["recent_hints_json"]),
        }

    def get_provider_binding(self, project_id: str, provider: str) -> str | None:
        if not project_id or not provider or not self.db_path.exists():
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT raw_session_id
                FROM provider_bindings
                WHERE project_id = ? AND provider = ?
                """,
                (project_id, provider),
            ).fetchone()
        if row is None:
            return None
        return (row["raw_session_id"] or "").strip() or None

    def get_runtime_health(self, project_id: str, provider: str | None = None) -> dict[str, Any] | dict[str, dict[str, Any]]:
        if not project_id or not self.db_path.exists():
            return {} if provider is None else {"project_id": project_id, "provider": provider or "", "status": "", "summary": "", "details": {}, "updated_at": ""}
        query = """
            SELECT project_id, provider, status, summary, details_json, updated_at
            FROM project_runtime_health
            WHERE project_id = ?
        """
        params: list[Any] = [project_id]
        if provider:
            query += " AND provider = ?"
            params.append(provider)
        try:
            with self._connect() as conn:
                rows = conn.execute(query, params).fetchall()
        except sqlite3.Error:
            rows = []
        payload: dict[str, dict[str, Any]] = {}
        for row in rows:
            details: dict[str, Any] = {}
            raw_details = str(row["details_json"] or "").strip()
            if raw_details:
                try:
                    parsed = json.loads(raw_details)
                    if isinstance(parsed, dict):
                        details = parsed
                except json.JSONDecodeError:
                    details = {}
            item = {
                "project_id": str(row["project_id"] or "").strip(),
                "provider": str(row["provider"] or "").strip(),
                "status": str(row["status"] or "").strip(),
                "summary": str(row["summary"] or "").strip(),
                "details": details,
                "updated_at": str(row["updated_at"] or "").strip(),
            }
            payload[item["provider"]] = item
        if provider:
            return payload.get(provider, {"project_id": project_id, "provider": provider, "status": "", "summary": "", "details": {}, "updated_at": ""})
        return payload

    def get_auto_continue_state(self, project_id: str, provider: str | None = None) -> dict[str, Any] | dict[str, dict[str, Any]]:
        if not project_id or not self.db_path.exists():
            return {} if provider is None else {"project_id": project_id, "provider": provider or "", "status": "", "summary": "", "details": {}, "updated_at": ""}
        query = """
            SELECT project_id, provider, status, summary, details_json, updated_at
            FROM project_auto_continue_state
            WHERE project_id = ?
        """
        params: list[Any] = [project_id]
        if provider:
            query += " AND provider = ?"
            params.append(provider)
        try:
            with self._connect() as conn:
                rows = conn.execute(query, params).fetchall()
        except sqlite3.Error:
            rows = []
        payload: dict[str, dict[str, Any]] = {}
        for row in rows:
            details: dict[str, Any] = {}
            raw_details = str(row["details_json"] or "").strip()
            if raw_details:
                try:
                    parsed = json.loads(raw_details)
                    if isinstance(parsed, dict):
                        details = parsed
                except json.JSONDecodeError:
                    details = {}
            item = {
                "project_id": str(row["project_id"] or "").strip(),
                "provider": str(row["provider"] or "").strip(),
                "status": str(row["status"] or "").strip(),
                "summary": str(row["summary"] or "").strip(),
                "details": details,
                "updated_at": str(row["updated_at"] or "").strip(),
            }
            payload[item["provider"]] = item
        if provider:
            return payload.get(provider, {"project_id": project_id, "provider": provider, "status": "", "summary": "", "details": {}, "updated_at": ""})
        return payload

    def record_runtime_health(
        self,
        project_id: str,
        provider: str,
        *,
        status: str,
        summary: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        if not project_id or not provider:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO project_runtime_health (project_id, provider, status, summary, details_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(project_id, provider) DO UPDATE SET
                        status = excluded.status,
                        summary = excluded.summary,
                        details_json = excluded.details_json,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        project_id,
                        provider,
                        status.strip(),
                        self._compact(summary, 220),
                        json.dumps(details or {}, ensure_ascii=False),
                    ),
                )
        except sqlite3.Error:
            return

    def record_auto_continue_state(
        self,
        project_id: str,
        provider: str,
        *,
        status: str,
        summary: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        if not project_id or not provider:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO project_auto_continue_state (project_id, provider, status, summary, details_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(project_id, provider) DO UPDATE SET
                        status = excluded.status,
                        summary = excluded.summary,
                        details_json = excluded.details_json,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        project_id,
                        provider,
                        status.strip(),
                        self._compact(summary, 220),
                        json.dumps(details or {}, ensure_ascii=False),
                    ),
                )
        except sqlite3.Error:
            return

    def clear_runtime_health(self, project_id: str, provider: str | None = None) -> None:
        if not project_id:
            return
        try:
            with self._connect() as conn:
                if provider:
                    conn.execute(
                        "DELETE FROM project_runtime_health WHERE project_id = ? AND provider = ?",
                        (project_id, provider),
                    )
                else:
                    conn.execute("DELETE FROM project_runtime_health WHERE project_id = ?", (project_id,))
        except sqlite3.Error:
            return

    def set_provider_binding(self, project_id: str, provider: str, raw_session_id: str) -> None:
        if not project_id or not provider or not raw_session_id:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO provider_bindings (project_id, provider, raw_session_id, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(project_id, provider) DO UPDATE SET
                        raw_session_id = excluded.raw_session_id,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (project_id, provider, raw_session_id),
                )
        except sqlite3.Error:
            return
        self.archive_other_project_sessions(project_id, provider, raw_session_id)

    def clear_provider_binding(self, project_id: str, provider: str) -> None:
        if not project_id or not provider:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM provider_bindings WHERE project_id = ? AND provider = ?",
                    (project_id, provider),
                )
        except sqlite3.Error:
            return

    def quarantine_provider_session(self, project_id: str, provider: str, session_id: str) -> None:
        if not project_id or not provider or not session_id:
            return
        try:
            with self._connect() as conn:
                tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                if "project_sessions" in tables:
                    conn.execute(
                        """
                        UPDATE project_sessions
                        SET status = 'quarantined', last_used_at = CURRENT_TIMESTAMP
                        WHERE project_id = ? AND provider = ? AND session_id = ?
                        """,
                        (project_id, provider, session_id),
                    )
                if "provider_sessions" in tables:
                    provider_columns = {row["name"] for row in conn.execute("PRAGMA table_info(provider_sessions)").fetchall()}
                    if "status" in provider_columns:
                        conn.execute(
                            """
                            UPDATE provider_sessions
                            SET status = 'quarantined', last_used_at = CURRENT_TIMESTAMP
                            WHERE project_id = ? AND provider = ? AND raw_session_id = ?
                            """,
                            (project_id, provider, session_id),
                        )
        except sqlite3.Error:
            return
        self.clear_provider_binding(project_id, provider)

    def archive_other_project_sessions(self, project_id: str, provider: str, keep_session_id: str) -> None:
        if not project_id or not provider or not keep_session_id or not self.db_path.exists():
            return
        try:
            with self._connect() as conn:
                tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                if "project_sessions" in tables:
                    conn.execute(
                        """
                        UPDATE project_sessions
                        SET status = 'archived', last_used_at = CURRENT_TIMESTAMP
                        WHERE project_id = ?
                          AND provider = ?
                          AND session_id != ?
                          AND status NOT IN ('archived', 'quarantined')
                        """,
                        (project_id, provider, keep_session_id),
                    )
                if "provider_sessions" in tables:
                    provider_columns = {row["name"] for row in conn.execute("PRAGMA table_info(provider_sessions)").fetchall()}
                    if "status" in provider_columns:
                        conn.execute(
                            """
                            UPDATE provider_sessions
                            SET status = 'archived', last_used_at = CURRENT_TIMESTAMP
                            WHERE project_id = ?
                              AND provider = ?
                              AND raw_session_id != ?
                              AND status NOT IN ('archived', 'quarantined')
                            """,
                            (project_id, provider, keep_session_id),
                        )
        except sqlite3.Error:
            return

    def upsert_project_memory(
        self,
        project_id: str,
        *,
        focus: str = "",
        current_state: str = "",
        recent_context: str = "",
        memory: str = "",
        recent_hints: list[str] | None = None,
    ) -> None:
        if not project_id:
            return
        normalized_state = (current_state or recent_context).strip()
        hints = [self._compact(item, _PROMPT_MESSAGE_LIMIT) for item in (recent_hints or []) if (item or '').strip()][:_PROMPT_RECENT_MESSAGE_COUNT]
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO project_memory (project_id, focus, current_state, recent_context, memory, recent_hints_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(project_id) DO UPDATE SET
                        focus = CASE WHEN excluded.focus != '' THEN excluded.focus ELSE project_memory.focus END,
                        current_state = CASE WHEN excluded.current_state != '' THEN excluded.current_state ELSE project_memory.current_state END,
                        recent_context = CASE WHEN excluded.recent_context != '' THEN excluded.recent_context ELSE project_memory.recent_context END,
                        memory = CASE WHEN excluded.memory != '' THEN excluded.memory ELSE project_memory.memory END,
                        recent_hints_json = CASE WHEN excluded.recent_hints_json != '[]' THEN excluded.recent_hints_json ELSE project_memory.recent_hints_json END,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (project_id, focus.strip(), normalized_state, normalized_state, memory.strip(), json.dumps(hints, ensure_ascii=False)),
                )
        except sqlite3.Error:
            return

    def upsert_project_session(
        self,
        project_id: str,
        provider: str,
        session_id: str,
        *,
        status: str = "active",
        title: str = "",
        summary: str = "",
        cwd: str = "",
        source_path: str = "",
        last_used_at: str = "",
    ) -> None:
        if not project_id or not provider or not session_id:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO project_sessions (
                        project_id, provider, session_id, status, title, summary, cwd, source_path, first_seen_at, last_used_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, COALESCE(NULLIF(?, ''), CURRENT_TIMESTAMP))
                    ON CONFLICT(provider, session_id) DO UPDATE SET
                        project_id = excluded.project_id,
                        status = excluded.status,
                        title = CASE WHEN excluded.title != '' THEN excluded.title ELSE project_sessions.title END,
                        summary = CASE WHEN excluded.summary != '' THEN excluded.summary ELSE project_sessions.summary END,
                        cwd = CASE WHEN excluded.cwd != '' THEN excluded.cwd ELSE project_sessions.cwd END,
                        source_path = CASE WHEN excluded.source_path != '' THEN excluded.source_path ELSE project_sessions.source_path END,
                        last_used_at = COALESCE(NULLIF(excluded.last_used_at, ''), CURRENT_TIMESTAMP)
                    """,
                    (
                        project_id,
                        provider,
                        session_id,
                        status,
                        title.strip(),
                        summary.strip(),
                        cwd.strip(),
                        source_path.strip(),
                        last_used_at.strip(),
                    ),
                )
        except sqlite3.Error:
            return

    def list_project_sessions(
        self,
        project_id: str,
        provider: str | None = None,
        include_archived: bool = True,
    ) -> list[dict[str, Any]]:
        if not project_id or not self.db_path.exists():
            return []
        query = """
            SELECT project_id, provider, session_id, status, title, summary, cwd, source_path, first_seen_at, last_used_at
            FROM project_sessions
            WHERE project_id = ?
        """
        params: list[str] = [project_id]
        if provider:
            query += " AND provider = ?"
            params.append(provider)
        if not include_archived:
            query += " AND status = 'active'"
        query += " ORDER BY provider ASC, last_used_at DESC, session_id DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def set_project_session_status(self, provider: str, session_id: str, status: str) -> None:
        if not provider or not session_id or not status:
            return
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE project_sessions
                SET status = ?, last_used_at = CURRENT_TIMESTAMP
                WHERE provider = ? AND session_id = ?
                """,
                (status, provider, session_id),
            )

    def get_current_project_sessions(self, project_id: str) -> dict[str, str]:
        if not project_id or not self.db_path.exists():
            return {}
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT provider, raw_session_id
                FROM provider_bindings
                WHERE project_id = ?
                ORDER BY provider ASC
                """,
                (project_id,),
            ).fetchall()
            tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            quarantined: set[tuple[str, str]] = set()
            if "project_sessions" in tables:
                quarantined.update(
                    (
                        str(row["provider"]).strip(),
                        str(row["session_id"]).strip(),
                    )
                    for row in conn.execute(
                        """
                        SELECT provider, session_id
                        FROM project_sessions
                        WHERE project_id = ? AND status = 'quarantined'
                        """,
                        (project_id,),
                    ).fetchall()
                )
            if "provider_sessions" in tables:
                provider_columns = {row["name"] for row in conn.execute("PRAGMA table_info(provider_sessions)").fetchall()}
                if "status" in provider_columns:
                    quarantined.update(
                        (
                            str(row["provider"]).strip(),
                            str(row["raw_session_id"]).strip(),
                        )
                        for row in conn.execute(
                            """
                            SELECT provider, raw_session_id
                            FROM provider_sessions
                            WHERE project_id = ? AND status = 'quarantined'
                            """,
                            (project_id,),
                        ).fetchall()
                    )
        bindings = {
            str(row["provider"]).strip(): str(row["raw_session_id"]).strip()
            for row in rows
            if (row["provider"] or "").strip() and (row["raw_session_id"] or "").strip()
        }
        filtered: dict[str, str] = {}
        for provider, session_id in bindings.items():
            if (provider, session_id) in quarantined:
                self.clear_provider_binding(project_id, provider)
                continue
            filtered[provider] = session_id
        return filtered

    def render_project_memory_markdown(self, project_id: str) -> str:
        project = self.get_project(project_id)
        if project is None:
            return ""
        stored_memory = self.get_project_memory(project_id)
        shared_scopes = self.resolve_project_memory_scopes(project_id)
        shared_snapshot = self.build_global_memory_snapshot(scopes=shared_scopes, include_global=False)
        global_snapshot = self.build_global_memory_snapshot(scopes=shared_scopes, include_global=True)
        bindings = self.get_current_project_sessions(project_id)
        sessions = self.list_project_sessions(project_id, include_archived=False)
        focus, current_state, memory, recent_hints = self._project_memory_values(project, stored_memory)
        daily_projection = self.derive_daily_projection(
            project_id=project_id,
            focus=focus,
            current_state=current_state,
            memory=memory,
            hints=recent_hints,
            workspace_path=project.workspace_path or "",
        )
        brief = self.derive_session_brief(
            focus=focus,
            recent_context=current_state,
            memory=memory,
            hints=recent_hints,
        )
        next_step = brief["next_step"]
        lines = [
            "# PROJECT_MEMORY",
            "",
            "## Role",
            "This file is a generated local memory view exported from the project state database.",
            f"The project-scoped OpenViking context store lives at `{project_ov_resource_uri(project.project_id)}`.",
            "",
            "## Project",
            project.project_id,
            "",
            "## Path",
            project.workspace_path or "",
            "",
            "## Summary",
            project.summary or "",
            "",
            "## Current Focus",
        ]
        ov_overview = self._project_ov_overview(project.project_id)
        if ov_overview:
            lines[6:6] = [
                "## OpenViking Overview",
                ov_overview,
                "",
            ]
        if focus:
            lines.append(focus)
        else:
            lines.append("No focus recorded yet.")
        lines.extend(
            [
                "",
                "## Current State",
            ]
        )
        if daily_projection["current_state"]:
            lines.append(daily_projection["current_state"])
        else:
            lines.append("No current state recorded yet.")
        lines.extend(
            [
                "",
                "## Daily Projection",
                f"- phase: {daily_projection['current_phase'] or 'active'}",
                f"- blocker: {daily_projection['current_blocker'] or 'none'}",
                f"- last_verified_result: {daily_projection['last_verified_result'] or 'none'}",
                f"- stable_memory_hint: {daily_projection['stable_memory_hint'] or 'none'}",
                f"- runtime_health: {self.build_memory_snapshot(project.workspace_path).get('runtime_health_summary') or 'none'}",
                f"- auto_continue: {self.build_memory_snapshot(project.workspace_path).get('auto_continue_summary') or 'none'}",
            ]
        )
        lines.extend(
            [
                "",
                "## Next Step",
            ]
        )
        if next_step:
            lines.append(next_step)
        else:
            lines.append("No next step recorded yet.")
        lines.extend(
            [
                "",
                "## Key Rules",
            ]
        )
        key_rules = [project.profile.strip(), stored_memory.get("memory", "").strip()]
        for item in key_rules:
            if item:
                lines.append(f"- {item}")
        if not any(item for item in key_rules):
            lines.append("- No key rules recorded yet.")
        workbench_memory_lines = self._workbench_memory_lines(project.project_id)
        if workbench_memory_lines:
            lines.extend(["", "## Workbench Runtime", *workbench_memory_lines])
        lines.extend(
            [
                "",
                "## Shared Memory Hooks",
            ]
        )
        if shared_scopes:
            for scope in shared_scopes:
                lines.append(f"- scope: {scope}")
        else:
            lines.append("- No shared scopes bound.")
        lines.append(f"- shared memory file: {self.db_path.parent / _GLOBAL_MEMORY_FILE}")
        if shared_snapshot["hints"]:
            lines.append("- active shared hints:")
            for item in shared_snapshot["hints"]:
                lines.append(f"  {item}")
        self._append_global_memory_section(lines, global_snapshot, heading="## Global Memory")
        lines.extend(
            [
                "",
                "## Current Sessions",
            ]
        )
        if bindings:
            for provider in sorted(bindings):
                lines.append(f"- {provider}: {bindings[provider]}")
        else:
            lines.append("- No current provider bindings.")
        lines.extend(
            [
                "",
                "## Active Session History",
            ]
        )
        if sessions:
            for session in sessions[:8]:
                title = self._compact(
                    " ".join(
                        str(session.get("title") or session.get("summary") or session.get("session_id") or "").split()
                    ),
                    96,
                )
                lines.append(
                    f"- {session.get('provider', '')}: {title} [{session.get('session_id', '')}]"
                )
        else:
            lines.append("- No active session history.")
        lines.extend(
            [
                "",
                "## Updated At",
                self._project_updated_at(project_id),
                "",
            ]
        )
        return "\n".join(lines)

    def render_project_summary(self, project_id: str) -> str:
        project = self.get_project(project_id)
        if project is None:
            return ""
        stored_memory = self.get_project_memory(project_id)
        bindings = self.get_current_project_sessions(project_id)
        sessions = self.list_project_sessions(project_id, include_archived=False)
        focus, current_state, memory, recent_hints = self._project_memory_values(project, stored_memory)
        daily_projection = self.derive_daily_projection(
            project_id=project_id,
            focus=focus,
            current_state=current_state,
            memory=memory,
            hints=recent_hints,
            workspace_path=project.workspace_path or "",
        )
        snapshot = self.build_memory_snapshot(project.workspace_path)
        brief = self.derive_session_brief(
            focus=focus,
            recent_context=current_state,
            memory=memory,
            hints=recent_hints,
        )
        lines = [
            f"Project: {project.project_id}",
            f"Workspace: {project.workspace_path or ''}",
        ]
        if brief["focus"]:
            lines.append(f"Current focus: {brief['focus']}")
        if daily_projection["current_phase"]:
            lines.append(f"Current phase: {daily_projection['current_phase']}")
        if daily_projection["current_state"]:
            lines.append(f"Current state: {daily_projection['current_state']}")
        if daily_projection["current_blocker"]:
            lines.append(f"Current blocker: {daily_projection['current_blocker']}")
        if brief["next_step"]:
            lines.append(f"Next step: {brief['next_step']}")
        if daily_projection["last_verified_result"]:
            lines.append(f"Last verified result: {daily_projection['last_verified_result']}")
        if snapshot.get("runtime_health_summary"):
            lines.append(f"Runtime health: {snapshot['runtime_health_summary']}")
        if snapshot.get("auto_continue_summary"):
            lines.append(f"Auto-continue: {snapshot['auto_continue_summary']}")
        if brief["memory"]:
            lines.append(f"Cache summary: {brief['memory']}")
        if bindings:
            providers = ", ".join(f"{provider}={bindings[provider]}" for provider in sorted(bindings))
            lines.append(f"Current sessions: {providers}")
        elif sessions:
            providers = ", ".join(
                f"{session.get('provider', '')}={session.get('session_id', '')}"
                for session in sessions[:2]
                if session.get("provider") and session.get("session_id")
            )
            if providers:
                lines.append(f"Current sessions: {providers}")
        ov_overview = self._project_ov_overview(project.project_id, limit=220)
        if ov_overview:
            lines.append(f"OpenViking overview: {ov_overview}")
        shared_scopes = self.resolve_project_memory_scopes(project_id)
        if shared_scopes:
            lines.append(f"Shared scopes: {', '.join(shared_scopes)}")
        return "\n".join(lines)

    def render_project_skill_markdown(self, project_id: str) -> str:
        project = self.get_project(project_id)
        if project is None:
            return ""
        stored_memory = self.get_project_memory(project_id)
        shared_scopes = self.resolve_project_memory_scopes(project_id)
        shared_snapshot = self.build_global_memory_snapshot(scopes=shared_scopes, include_global=False)
        global_snapshot = self.build_global_memory_snapshot(scopes=shared_scopes, include_global=True)
        focus, recent_context, _memory, _recent_hints = self._project_memory_values(project, stored_memory)
        ov_overview = self._project_ov_overview(project.project_id, limit=420)
        lines = [
            "# PROJECT_SKILL",
            "",
            "## Role",
            "This file is a generated local rules/startup view exported from the project state and OV project context.",
            "",
            "## Startup",
            f"- Treat `{project_ov_resource_uri(project.project_id)}` as the project-scoped context store when OpenViking is available.",
            f"- Use `PROJECT_MEMORY.md` in `{project.workspace_path}` as the local exported startup view.",
            "- Treat `Current Focus` as the default priority unless the user changes direction.",
            "- Treat `Current State` as the latest known status, not as a full history replay.",
            "",
        ]
        if ov_overview:
            lines.extend(
                [
                    "## OpenViking Context Notes",
                    ov_overview,
                    "",
                ]
            )
        lines.extend(
            [
            "## Work Rules",
            ]
        )
        work_rules = [
            "Prefer continuing the current project thread over starting a new framing from scratch.",
            "Keep outputs, notes, and temporary artifacts inside the project workspace when possible.",
            focus and f"Default focus: {focus}",
            recent_context and f"Latest known state: {recent_context}",
        ]
        for item in work_rules:
            if item:
                lines.append(f"- {item}")
        lines.extend(
            [
                "",
                "## Memory Rules",
                "- Project-scoped context belongs in OpenViking under the project resource directory when OV is enabled.",
                "- Cross-project rules and preferences belong in shared/global memory, not repeated per-project state.",
                "- Local project files are generated exported views; refresh them through the shared sync flow instead of treating them as the primary source of truth.",
                "- Do not overwrite project memory with meta chat such as asking whether memory exists.",
                "- When the task meaningfully changes, update project memory through the shared sync flow.",
            ]
        )
        lines.extend(
            [
                "",
                "## Automation Protocol",
                "- Canonical repo skill: `skills/automation-runtime/SKILL.md`.",
                "- Treat automation as a layered protocol: skill decides whether to trigger, adapter provides the chat entry, runtime commands execute, and runtime health gates unsafe paths.",
                "- Use `/autostep [provider] [--explain]` for one bounded automatic increment inside the current project.",
                "- Use `/automonitor [--apply] [--auto-continue] [--until-complete] [--cycles N] [--interval N]` for bounded monitor loops inside the current project.",
                "- If the user asks to periodically check project/task status without giving timing, ask one short clarifying question first: `隔多久看一次？`",
                "- Treat watch-only requests as monitor-first behavior without `--auto-continue`; only add `--apply --auto-continue` when the user explicitly wants the task to keep moving.",
                "- Default heartbeat/monitor behavior should remain project-scoped; only use all-project sweeps when the task is explicit runtime maintenance across projects.",
                "- Before starting automation, first derive one concrete next step for the current project; if the next step is still generic or ambiguous, explain it and ask for confirmation before execution.",
                "- Use `project-sessions auto-continue <project> [--provider ...] [--explain]` when you need the same one-step automation from the CLI side.",
                "- Use `project-sessions monitor <project> [--apply] [--auto-continue] [--until-complete] [--cycles N] [--interval N]` when you need repeated heartbeat probes and optional single-step continuation from the CLI side.",
                "- Keep automation bounded: one `autostep` executes at most one meaningful increment, and one monitor cycle triggers at most one auto-continue per eligible project.",
                "- `--until-complete` means stop early when heartbeat remains healthy but no further stable auto-continue candidate is available, or when structured completion checks return `completed`, `blocked`, or `needs_confirmation`.",
                "- Stop automation when runtime health is blocked (`quarantined`, `unhealthy`, `orphan-running`, `missing-binding-process`) or when no stable next step is available.",
                "- Prefer `--explain` first when the next step is unclear or when you need to validate the plan before execution.",
            ]
        )
        workbench_skill_lines = self._workbench_skill_lines(project.project_id)
        if workbench_skill_lines:
            lines.extend(["", "## Workbench Protocol", *workbench_skill_lines])
        lines.extend(
            [
                "",
                "## Shared Memory Hooks",
            ]
        )
        if shared_scopes:
            for scope in shared_scopes:
                lines.append(f"- Attach shared rules from `{scope}` when they match the task.")
        else:
            lines.append("- No shared scopes bound for this project yet.")
        lines.append(f"- Read shared rules from `{self.db_path.parent / _GLOBAL_MEMORY_FILE}` when you need cross-project guidance.")
        if shared_snapshot["hints"]:
            lines.append("- Current shared hints:")
            for item in shared_snapshot["hints"]:
                lines.append(f"- {item}")
        self._append_global_memory_section(lines, global_snapshot, heading="## Shared Global Memory")
        lines.extend(
            [
                "",
                "## Files",
                f"- Workspace: `{project.workspace_path}`",
                f"- OpenViking project context: `{project_ov_resource_uri(project.project_id)}`",
                "- Local exported memory view: `PROJECT_MEMORY.md`",
                "- Local exported rules view: `PROJECT_SKILL.md`",
                f"- Shared global memory view: `{self.db_path.parent / _GLOBAL_MEMORY_FILE}`",
                "",
            ]
        )
        return "\n".join(lines)

    def _workbench_memory_lines(self, project_id: str) -> list[str]:
        if project_id != _WORKBENCH_PROJECT:
            return []
        return [
            "- This project is the runtime workbench for tmux, ccb, ssh panes, and follow-up orchestration.",
            "- Track default pane layouts, preferred ssh targets, pane roles, and recent bridge/runtime outcomes here.",
            "- Keep business-project conclusions out of this project; store only workbench defaults, runtime constraints, and operator-facing status.",
            "- Default light layout: one agent pane plus one ssh pane; add `manual` or a second agent only when the task needs them.",
            "- Treat pane state, bridge policy, and ccb/tmux coordination as the primary memory concerns for this project.",
        ]

    def _workbench_skill_lines(self, project_id: str) -> list[str]:
        if project_id != _WORKBENCH_PROJECT:
            return []
        return [
            "- Canonical repo skill: `skills/ash-workbench/SKILL.md`.",
            "- Treat this project as the control plane for tmux workbenches, ccb-linked provider panes, and ssh panes.",
            "- Default to the lightest viable layout: `agent + ssh`, then add `manual` or `secondary agent` only when the task benefits from them.",
            "- Use the workbench project to manage pane roles, bridge policy, follow-up cadence, and runtime visibility, not to store domain conclusions from business projects.",
            "- Prefer `bridge-workbench` as the user-facing workspace entry; keep `project-sessions ...` as the lower-level control surface.",
        ]

    def sync_project_memory_file(self, project_id: str) -> Path | None:
        project = self.get_project(project_id)
        if project is None or not (project.workspace_path or "").strip():
            return None
        self.sync_project_summary(project_id)
        project = self.get_project(project_id)
        if project is None:
            return None
        workspace = Path(project.workspace_path).expanduser()
        try:
            workspace.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        output_path = workspace / "PROJECT_MEMORY.md"
        try:
            output_path.write_text(self.render_project_memory_markdown(project_id), encoding="utf-8")
        except OSError:
            return None
        return output_path

    def sync_project_skill_file(self, project_id: str) -> Path | None:
        project = self.get_project(project_id)
        if project is None or not (project.workspace_path or "").strip():
            return None
        self.sync_project_summary(project_id)
        project = self.get_project(project_id)
        if project is None:
            return None
        workspace = Path(project.workspace_path).expanduser()
        try:
            workspace.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        output_path = workspace / "PROJECT_SKILL.md"
        try:
            output_path.write_text(self.render_project_skill_markdown(project_id), encoding="utf-8")
        except OSError:
            return None
        return output_path

    def sync_all_project_memory_files(self) -> list[Path]:
        written: list[Path] = []
        for project in self.list_projects():
            path = self.sync_project_memory_file(project.project_id)
            if path is not None:
                written.append(path)
            skill_path = self.sync_project_skill_file(project.project_id)
            if skill_path is not None:
                written.append(skill_path)
        global_path = self.sync_global_memory_file()
        if global_path is not None:
            written.append(global_path)
        return written

    def render_global_memory_markdown(self) -> str:
        rows = self.list_global_memory(limit=64, scopes=self.list_memory_scopes(), include_global=True)
        lines = [
            "# SHARED_MEMORY",
            "",
            "## Role",
            "This file is the generated shared-memory view exported from the shared project state database.",
            "",
            "## Shared And Global Rules",
        ]
        if rows:
            for row in rows:
                category = str(row.get("category") or "workflow").strip()
                source_project = str(row.get("source_project_id") or "").strip()
                scope = str(row.get("scope") or "global").strip() or "global"
                suffix = f" [{scope} / {category}]" + (f" (source: {source_project})" if source_project else "")
                lines.append(f"- {row.get('content', '')}{suffix}")
        else:
            lines.append("- No shared or global memory recorded yet.")
        lines.extend(["", "## Updated At", self._global_memory_updated_at(), ""])
        return "\n".join(lines)

    def list_memory_scopes(self) -> list[str]:
        if not self.db_path.exists():
            return []
        try:
            with self._connect() as conn:
                global_rows = conn.execute(
                    """
                    SELECT DISTINCT scope
                    FROM global_memory
                    WHERE COALESCE(scope, '') != '' AND scope != 'global'
                    ORDER BY scope ASC
                    """
                ).fetchall()
                binding_rows = conn.execute(
                    """
                    SELECT DISTINCT scope
                    FROM project_memory_scopes
                    WHERE COALESCE(scope, '') != ''
                    ORDER BY scope ASC
                    """
                ).fetchall()
        except sqlite3.Error:
            return []
        scopes = {
            str(row["scope"]).strip()
            for row in [*global_rows, *binding_rows]
            if str(row["scope"]).strip()
        }
        return sorted(scopes)

    def sync_global_memory_file(self) -> Path | None:
        output_path = self.db_path.parent / _GLOBAL_MEMORY_FILE
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(self.render_global_memory_markdown(), encoding="utf-8")
        except OSError:
            return None
        return output_path

    def sync_project_summary(self, project_id: str) -> bool:
        if not project_id or not self.db_path.exists():
            return False
        summary = self.render_project_summary(project_id)
        if not summary:
            return False
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE projects
                    SET summary = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE project_id = ?
                    """,
                    (summary, project_id),
                )
        except sqlite3.Error:
            return False
        return True

    def consolidate_project_memory(
        self,
        project_id: str,
        *,
        live_summary: str = "",
        recent_messages: list[str] | None = None,
        executor: Executor | None = None,
    ) -> bool:
        project = self.get_project(project_id)
        if project is None:
            return False
        stored_memory = self.get_project_memory(project_id)
        bindings = self.get_current_project_sessions(project_id)
        sessions = self.list_project_sessions(project_id, include_archived=False)
        prompt = self._build_memory_consolidation_prompt(
            project_id=project.project_id,
            workspace_path=project.workspace_path,
            profile=project.profile,
            current_summary=project.summary,
            stored_memory=stored_memory,
            current_sessions=bindings,
            active_sessions=sessions[:6],
            live_summary=live_summary,
            recent_messages=recent_messages or [],
        )
        try:
            runner = executor or build_executor_for_config(
                mode="claude",
                transport="auto",
                work_dir=project.workspace_path or None,
                timeout_s=45,
            )
            result = runner.run(prompt)
            payload = self._parse_memory_consolidation_output(result.output)
        except Exception:
            return False
        focus = self._compact(str(payload.get("focus") or ""), _PROMPT_FIELD_LIMIT)
        current_state = self._compact(str(payload.get("current_state") or ""), _PROMPT_FIELD_LIMIT)
        next_step = self._compact(str(payload.get("next_step") or ""), _PROMPT_FIELD_LIMIT)
        long_term_memory = self._compact(str(payload.get("long_term_memory") or ""), _PROMPT_FIELD_LIMIT)
        key_points = [
            self._compact(str(item or ""), _PROMPT_MESSAGE_LIMIT)
            for item in list(payload.get("key_points") or [])
            if str(item or "").strip()
        ][:3]
        if next_step and next_step not in key_points:
            key_points = (key_points + [next_step])[:3]
        if not any((focus, current_state, long_term_memory, key_points)):
            return False
        self.upsert_project_memory(
            project_id,
            focus=focus,
            recent_context=current_state,
            memory=long_term_memory,
            recent_hints=key_points,
        )
        self.promote_project_memory_to_global(
            project_id,
            focus=focus,
            recent_context=current_state,
            memory=long_term_memory,
            recent_hints=key_points,
            ai_candidates=(
                self._parse_ai_global_memory_candidates(payload.get("global_memory_candidates") or [])
                if self._ai_global_memory_candidates_enabled()
                else []
            ),
        )
        self.sync_project_summary(project_id)
        return True

    @staticmethod
    def _compact(text: str | None, limit: int) -> str:
        value = " ".join((text or "").split())
        if len(value) <= limit:
            return value
        return value[: max(0, limit - 3)].rstrip() + "..."

    @staticmethod
    def _summary_field(summary: str | None, prefix: str) -> str:
        for line in (summary or "").splitlines():
            stripped = line.strip()
            if stripped.startswith(prefix):
                return stripped.removeprefix(prefix).strip()
        return ""

    @classmethod
    def _summary_state(cls, summary: str | None) -> str:
        return cls._summary_field(summary, "Current state:") or cls._summary_field(summary, "Recent context:")

    @classmethod
    def _summary_compact_text(cls, summary: str | None) -> str:
        parts: list[str] = []
        for line in (summary or "").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if ":" in stripped:
                key, value = stripped.split(":", 1)
                if key.strip() in {"Project", "Workspace", "Current focus", "Recent context"}:
                    continue
                stripped = value.strip() or stripped
            parts.append(stripped)
        return " | ".join(parts[:2])

    def _build_memory_consolidation_prompt(
        self,
        *,
        project_id: str,
        workspace_path: str,
        profile: str,
        current_summary: str,
        stored_memory: dict[str, Any],
        current_sessions: dict[str, str],
        active_sessions: list[dict[str, Any]],
        live_summary: str,
        recent_messages: list[str],
    ) -> str:
        session_lines = [
            f"- {provider}: {session_id}"
            for provider, session_id in sorted(current_sessions.items())
            if provider and session_id
        ] or ["- none"]
        active_session_lines = [
            f"- {session.get('provider', '')}: {session.get('title') or session.get('summary') or session.get('session_id') or ''}"
            for session in active_sessions
        ] or ["- none"]
        message_lines = [f"- {self._compact(item, 180)}" for item in recent_messages if (item or "").strip()] or ["- none"]
        return "\n".join(
            [
                "You are consolidating project memory for a coding workspace.",
                "Return JSON only. No markdown fences.",
                'Schema: {"focus":"...","current_state":"...","next_step":"...","long_term_memory":"...","key_points":["..."],"global_memory_candidates":[{"content":"...","category":"environment|preference|workflow","confidence":0.0,"reason":"..."}]}',
                "Rules:",
                "- Focus on durable project understanding, not chat meta.",
                "- current_state should describe the latest meaningful situation in 1 sentence.",
                "- next_step should be the single most important next action.",
                "- long_term_memory should capture stable constraints, decisions, or strategy.",
                "- key_points should contain at most 3 short bullets worth remembering.",
                "- global_memory_candidates should contain at most 3 durable cross-project rules, defaults, or preferences.",
                "- Only emit a global_memory_candidate when the content would still be useful in other projects.",
                "- Never emit project-specific tasks, temporary statuses, or one-off troubleshooting notes as global memory.",
                "- Do not include direct project names or absolute paths in global_memory_candidates; abstract them into reusable rules.",
                "- confidence should be between 0.0 and 1.0.",
                "",
                f"Project: {project_id}",
                f"Workspace: {workspace_path}",
                f"Profile: {profile or 'none'}",
                "",
                "Current structured summary:",
                current_summary or "none",
                "",
                "Stored memory:",
                f"- focus: {stored_memory.get('focus') or 'none'}",
                f"- current_state: {stored_memory.get('recent_context') or 'none'}",
                f"- long_term_memory: {stored_memory.get('memory') or 'none'}",
                f"- key_points: {', '.join(stored_memory.get('recent_hints') or []) or 'none'}",
                "",
                "Current provider bindings:",
                *session_lines,
                "",
                "Active session inventory:",
                *active_session_lines,
                "",
                "Live runtime summary:",
                live_summary or "none",
                "",
                "Recent meaningful messages:",
                *message_lines,
            ]
        )

    @staticmethod
    def _parse_memory_consolidation_output(raw: str) -> dict[str, Any]:
        text = (raw or "").strip()
        if not text:
            return {}
        try:
            payload = json.loads(text)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            pass
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                payload = json.loads(text[start : end + 1])
                return payload if isinstance(payload, dict) else {}
            except Exception:
                return {}
        return {}

    @classmethod
    def _parse_ai_global_memory_candidates(cls, raw_candidates: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_candidates, list):
            return []
        parsed: list[dict[str, Any]] = []
        for item in raw_candidates[:6]:
            if not isinstance(item, dict):
                continue
            content = cls._compact(cls._strip_memory_label(str(item.get("content") or "")), 280)
            reason = cls._compact(str(item.get("reason") or ""), 160)
            category = str(item.get("category") or "").strip().lower() or "workflow"
            if category not in {"environment", "preference", "workflow"}:
                category = "workflow"
            try:
                confidence = float(item.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            parsed.append(
                {
                    "content": content,
                    "reason": reason,
                    "category": category,
                    "confidence": min(1.0, max(0.0, confidence)),
                }
            )
        return parsed

    @staticmethod
    def _ai_global_memory_candidates_enabled() -> bool:
        raw = (os.getenv("ASH_ENABLE_AI_GLOBAL_MEMORY_CANDIDATES") or "").strip().casefold()
        if raw in {"0", "false", "off", "no"}:
            return False
        return True

    @staticmethod
    def _global_memory_promotion_mode() -> str:
        raw = (os.getenv("ASH_GLOBAL_MEMORY_PROMOTION_MODE") or "").strip().casefold()
        if raw in {"ai-only", "rules-only", "hybrid"}:
            return raw
        return "hybrid"

    @classmethod
    def _parse_hints_json(cls, raw: str | None) -> list[str]:
        if not raw:
            return []
        try:
            payload = json.loads(raw)
        except Exception:
            return []
        if not isinstance(payload, list):
            return []
        return [
            cls._compact(str(item), _PROMPT_MESSAGE_LIMIT)
            for item in payload
            if str(item).strip()
        ][:_PROMPT_RECENT_MESSAGE_COUNT]

    def _project_updated_at(self, project_id: str) -> str:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT MAX(value) AS updated_at
                    FROM (
                        SELECT updated_at AS value FROM projects WHERE project_id = ?
                        UNION ALL
                        SELECT updated_at AS value FROM project_memory WHERE project_id = ?
                        UNION ALL
                        SELECT updated_at AS value FROM provider_bindings WHERE project_id = ?
                    )
                    """,
                    (project_id, project_id, project_id),
                ).fetchone()
        except sqlite3.Error:
            return ""
        return str(row["updated_at"] or "").strip() or ""

    def _global_memory_updated_at(self) -> str:
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT MAX(updated_at) AS updated_at FROM global_memory").fetchone()
        except sqlite3.Error:
            return ""
        return str((row["updated_at"] if row else "") or "").strip() or ""

    @classmethod
    def _empty_memory_snapshot(cls, global_snapshot: dict[str, Any]) -> dict[str, Any]:
        return {
            "project_id": "",
            "workspace": "",
            "profile": "",
            "focus": "",
            "current_state": "",
            "recent_context": "",
            "memory": "",
            "recent_hints": [],
            "current_phase": "",
            "current_blocker": "",
            "last_verified_result": "",
            "stable_memory_hint": "",
            "daily_state_source": "",
            "runtime_health_status": "",
            "runtime_health_summary": "",
            "runtime_health_updated_at": "",
            "runtime_health_details": {},
            "auto_continue_status": "",
            "auto_continue_summary": "",
            "auto_continue_updated_at": "",
            "auto_continue_details": {},
            "global_memory": global_snapshot["summary"],
            "global_hints": global_snapshot["hints"],
            "shared_memory": "",
            "shared_hints": [],
            "shared_scopes": [],
            "universal_memory": global_snapshot["summary"],
            "universal_hints": global_snapshot["hints"],
        }

    def _project_memory_values(
        self,
        project: ProjectContext,
        stored_memory: dict[str, Any],
    ) -> tuple[str, str, str, list[str]]:
        recent_hints = stored_memory["recent_hints"] or [
            self._compact(message, _PROMPT_MESSAGE_LIMIT)
            for message in project.recent_messages[-_PROMPT_RECENT_MESSAGE_COUNT:]
            if (message or "").strip()
        ]
        focus = stored_memory["focus"] or self._summary_field(project.summary, "Current focus:")
        recent_context = stored_memory["recent_context"] or self._summary_state(project.summary)
        memory = stored_memory["memory"] or (
            self._compact(self._summary_compact_text(project.summary), _PROMPT_FIELD_LIMIT) if project.summary else ""
        )
        return focus, recent_context, memory, recent_hints

    def _project_ov_overview(self, project_id: str, *, limit: int | None = None) -> str:
        overview = read_openviking_overview(project_ov_resource_uri(project_id))
        if limit is None:
            return overview
        return self._compact(overview, limit)

    @classmethod
    def _append_global_memory_section(
        cls,
        lines: list[str],
        global_snapshot: dict[str, Any],
        *,
        heading: str,
    ) -> None:
        lines.extend(["", heading])
        if global_snapshot["hints"]:
            for item in global_snapshot["hints"]:
                lines.append(f"- {item}")
        else:
            lines.append("- No shared cross-project memory recorded yet.")

    @classmethod
    def _derive_next_step(
        cls,
        *,
        focus: str,
        current_state: str,
        memory: str,
        hints: list[str],
    ) -> str:
        for hint in reversed(hints):
            value = " ".join(str(hint).split())
            if value:
                value = cls._strip_memory_label(value)
                if value:
                    return cls._compact(value, 220)
        if current_state:
            return cls._compact(current_state, 220)
        if memory:
            return cls._compact(cls._strip_memory_label(memory), 220)
        return cls._compact(focus, 220) if focus else ""

    @classmethod
    def _strip_memory_label(cls, value: str | None) -> str:
        text = " ".join((value or "").split()).strip()
        if not text:
            return ""
        changed = True
        while changed:
            changed = False
            for prefix in (
                "user:",
                "assistant:",
                "Task:",
                "State:",
                "Latest result:",
                "Recent:",
                "Recent context:",
                "Current focus:",
            ):
                if text.startswith(prefix):
                    text = text.removeprefix(prefix).strip()
                    changed = True
        return text

    @classmethod
    def _memory_values_match(cls, left: str | None, right: str | None) -> bool:
        normalized_left = cls._strip_memory_label(left).casefold()
        normalized_right = cls._strip_memory_label(right).casefold()
        return bool(normalized_left and normalized_right and normalized_left == normalized_right)

    @classmethod
    def _global_memory_key(cls, content: str) -> str:
        return cls._scoped_memory_key("global", content)

    @classmethod
    def _scoped_memory_key(cls, scope: str, content: str) -> str:
        normalized = f"{cls._normalize_memory_scope(scope) or 'global'}::{cls._strip_memory_label(content).casefold()}"
        return sha1(normalized.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def _normalize_memory_scope(cls, scope: str | None) -> str:
        normalized = " ".join((scope or "").split()).strip().casefold()
        if not normalized:
            return ""
        if normalized == "global":
            return "global"
        if normalized.startswith("shared:") and len(normalized) > len("shared:"):
            return normalized
        if normalized.startswith("shared/") and len(normalized) > len("shared/"):
            return "shared:" + normalized.removeprefix("shared/")
        return f"shared:{normalized}"

    @classmethod
    def _is_global_memory_candidate(cls, project_id: str, text: str) -> bool:
        normalized = cls._strip_memory_label(text)
        lowered = normalized.casefold()
        if len(normalized) < 12:
            return False
        if project_id and project_id.casefold() in lowered:
            return False
        scope_markers = (
            "本地",
            "服务器",
            "代理",
            "全局",
            "跨项目",
            "shared",
            "global",
            "local",
            "server",
            "proxy",
            "tmux",
            "mcp",
            "mac",
        )
        directive_markers = (
            "默认",
            "总是",
            "不要",
            "优先",
            "保持",
            "禁用",
            "开启",
            "关闭",
            "prefer",
            "default",
            "always",
            "never",
            "avoid",
            "should",
        )
        return any(marker in lowered for marker in scope_markers) and any(marker in lowered for marker in directive_markers)

    @classmethod
    def _global_memory_category(cls, text: str) -> str:
        lowered = cls._strip_memory_label(text).casefold()
        if any(token in lowered for token in ("proxy", "代理", "mcp", "tmux", "server", "mac", "local")):
            return "environment"
        if any(token in lowered for token in ("prefer", "默认", "优先", "不要", "总是")):
            return "preference"
        return "workflow"

    @classmethod
    def _looks_like_path(cls, text: str) -> bool:
        lowered = cls._strip_memory_label(text).casefold()
        return any(
            token in lowered
            for token in (
                "/users/",
                "/home/",
                "/tmp/",
                "/var/",
                "file://",
                "\\users\\",
                ":\\",
            )
        )

    @classmethod
    def _looks_like_runtime_noise(cls, text: str) -> bool:
        lowered = cls._strip_memory_label(text).casefold()
        noise_markers = (
            "task id:",
            "phase:",
            "backend:",
            "pending=",
            "completed=",
            "in_progress",
            "blocked=",
            "recent: no notable",
            "no notable updates",
        )
        return any(marker in lowered for marker in noise_markers)

    @classmethod
    def _looks_like_project_description(cls, text: str) -> bool:
        lowered = cls._strip_memory_label(text).casefold()
        description_markers = (
            "基于 openviking 平台进行",
            "项目，重点是",
            "project involves",
            "core workspace",
            "core project workspace",
            "核心策略是",
        )
        return any(marker in lowered for marker in description_markers)

    @classmethod
    def _accept_global_memory_candidate(cls, project_id: str, text: str, *, from_ai: bool = False) -> bool:
        normalized = cls._strip_memory_label(text)
        lowered = normalized.casefold()
        if cls._looks_like_runtime_noise(normalized):
            return False
        if project_id and project_id.casefold() in lowered:
            return False
        if cls._looks_like_path(normalized):
            return False
        if cls._looks_like_project_description(normalized):
            return False
        if from_ai:
            return len(normalized) >= 12
        if not cls._is_global_memory_candidate(project_id, normalized):
            return False
        return True

    @classmethod
    def _global_memory_candidates(
        cls,
        project_id: str,
        *,
        focus: str,
        recent_context: str,
        memory: str,
        recent_hints: list[str],
        ai_candidates: list[dict[str, Any]] | None = None,
        mode: str = "hybrid",
    ) -> list[dict[str, Any]]:
        seen: set[str] = set()
        candidates: list[dict[str, Any]] = []
        if mode in {"hybrid", "ai-only"}:
            for item in ai_candidates or []:
                normalized = cls._strip_memory_label(str(item.get("content") or ""))
                key = normalized.casefold()
                if not normalized or key in seen:
                    continue
                seen.add(key)
                if not cls._accept_global_memory_candidate(project_id, normalized, from_ai=True):
                    continue
                candidates.append(
                    {
                        "content": normalized,
                        "category": str(item.get("category") or cls._global_memory_category(normalized)),
                        "confidence": max(0.7, min(0.95, float(item.get("confidence") or 0.0) or 0.7)),
                    }
                )
        if mode in {"hybrid", "rules-only"}:
            for raw in [memory, *recent_hints, recent_context, focus]:
                normalized = cls._strip_memory_label(raw)
                key = normalized.casefold()
                if not normalized or key in seen:
                    continue
                seen.add(key)
                if not cls._accept_global_memory_candidate(project_id, normalized):
                    continue
                confidence = 0.8 if raw == memory else 0.65
                candidates.append(
                    {
                        "content": normalized,
                        "category": cls._global_memory_category(normalized),
                        "confidence": confidence,
                    }
                )
        return candidates
