from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import project_session_db_path


_PROMPT_PROFILE_LIMIT = 160
_PROMPT_FIELD_LIMIT = 180
_PROMPT_MESSAGE_LIMIT = 120
_PROMPT_RECENT_MESSAGE_COUNT = 2


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
                """
            )

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
        if snapshot["recent_context"]:
            lines.append(f"Recent Context: {snapshot['recent_context']}")
        elif snapshot["memory"]:
            lines.append(f"Project Memory: {snapshot['memory']}")
        if snapshot["recent_hints"]:
            lines.append("Recent Memory Hints:")
            lines.extend(f"- {message}" for message in snapshot["recent_hints"])
        return "\n".join(lines)

    def build_memory_snapshot(self, workspace_path: str | None) -> dict[str, Any]:
        project = self.get_for_workspace_path(workspace_path)
        if project is None:
            return {
                "project_id": "",
                "workspace": "",
                "profile": "",
                "focus": "",
                "recent_context": "",
                "memory": "",
                "recent_hints": [],
            }
        stored_memory = self.get_project_memory(project.project_id)
        recent_messages = stored_memory["recent_hints"] or [
            self._compact(message, _PROMPT_MESSAGE_LIMIT)
            for message in project.recent_messages[-_PROMPT_RECENT_MESSAGE_COUNT:]
            if (message or "").strip()
        ]
        focus = stored_memory["focus"] or self._summary_field(project.summary, "Current focus:")
        recent_context = stored_memory["recent_context"] or self._summary_field(project.summary, "Recent context:")
        memory = stored_memory["memory"] or (
            self._compact(self._summary_compact_text(project.summary), _PROMPT_FIELD_LIMIT) if project.summary else ""
        )
        return {
            "project_id": project.project_id,
            "workspace": self._compact(project.workspace_path, _PROMPT_FIELD_LIMIT),
            "profile": self._compact(project.profile, _PROMPT_PROFILE_LIMIT),
            "focus": self._compact(focus, _PROMPT_FIELD_LIMIT) if focus else "",
            "recent_context": self._compact(recent_context, _PROMPT_FIELD_LIMIT) if recent_context else "",
            "memory": self._compact(memory, _PROMPT_FIELD_LIMIT) if memory else "",
            "recent_hints": recent_messages,
        }

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
            "recent_context": normalized_state,
            "next_step": next_step,
            "memory": memory_brief,
        }

    def get_project_memory(self, project_id: str) -> dict[str, Any]:
        if not project_id or not self.db_path.exists():
            return {"focus": "", "recent_context": "", "memory": "", "recent_hints": []}
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT focus, recent_context, memory, recent_hints_json
                FROM project_memory
                WHERE project_id = ?
                """,
                (project_id,),
            ).fetchone()
        if row is None:
            return {"focus": "", "recent_context": "", "memory": "", "recent_hints": []}
        return {
            "focus": self._compact(row["focus"], _PROMPT_FIELD_LIMIT) if row["focus"] else "",
            "recent_context": self._compact(row["recent_context"], _PROMPT_FIELD_LIMIT) if row["recent_context"] else "",
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
                        WHERE project_id = ? AND provider = ? AND session_id != ? AND status != 'archived'
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
                            WHERE project_id = ? AND provider = ? AND raw_session_id != ? AND status != 'archived'
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
        recent_context: str = "",
        memory: str = "",
        recent_hints: list[str] | None = None,
    ) -> None:
        if not project_id:
            return
        hints = [self._compact(item, _PROMPT_MESSAGE_LIMIT) for item in (recent_hints or []) if (item or '').strip()][:_PROMPT_RECENT_MESSAGE_COUNT]
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO project_memory (project_id, focus, recent_context, memory, recent_hints_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(project_id) DO UPDATE SET
                        focus = CASE WHEN excluded.focus != '' THEN excluded.focus ELSE project_memory.focus END,
                        recent_context = CASE WHEN excluded.recent_context != '' THEN excluded.recent_context ELSE project_memory.recent_context END,
                        memory = CASE WHEN excluded.memory != '' THEN excluded.memory ELSE project_memory.memory END,
                        recent_hints_json = CASE WHEN excluded.recent_hints_json != '[]' THEN excluded.recent_hints_json ELSE project_memory.recent_hints_json END,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (project_id, focus.strip(), recent_context.strip(), memory.strip(), json.dumps(hints, ensure_ascii=False)),
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
        return {
            str(row["provider"]).strip(): str(row["raw_session_id"]).strip()
            for row in rows
            if (row["provider"] or "").strip() and (row["raw_session_id"] or "").strip()
        }

    def render_project_memory_markdown(self, project_id: str) -> str:
        project = self.get_project(project_id)
        if project is None:
            return ""
        stored_memory = self.get_project_memory(project_id)
        bindings = self.get_current_project_sessions(project_id)
        sessions = self.list_project_sessions(project_id, include_archived=False)
        focus = stored_memory.get("focus", "") or self._summary_field(project.summary, "Current focus:")
        current_state = stored_memory.get("recent_context", "") or self._summary_field(project.summary, "Recent context:")
        brief = self.derive_session_brief(
            focus=focus,
            recent_context=current_state,
            memory=stored_memory.get("memory", ""),
            hints=stored_memory.get("recent_hints", []),
        )
        next_step = brief["next_step"]
        lines = [
            "# PROJECT_MEMORY",
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
        if current_state:
            lines.append(current_state)
        else:
            lines.append("No current state recorded yet.")
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
        brief = self.derive_session_brief(
            focus=stored_memory.get("focus", "") or self._summary_field(project.summary, "Current focus:"),
            recent_context=stored_memory.get("recent_context", "") or self._summary_field(project.summary, "Recent context:"),
            memory=stored_memory.get("memory", "") or self._summary_compact_text(project.summary),
            hints=stored_memory.get("recent_hints", []),
        )
        lines = [
            f"Project: {project.project_id}",
            f"Workspace: {project.workspace_path or ''}",
        ]
        if brief["focus"]:
            lines.append(f"Current focus: {brief['focus']}")
        if brief["recent_context"]:
            lines.append(f"Recent context: {brief['recent_context']}")
        if brief["next_step"]:
            lines.append(f"Next step: {brief['next_step']}")
        if brief["memory"]:
            lines.append(f"Long-term memory: {brief['memory']}")
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
        return "\n".join(lines)

    def render_project_skill_markdown(self, project_id: str) -> str:
        project = self.get_project(project_id)
        if project is None:
            return ""
        stored_memory = self.get_project_memory(project_id)
        focus = stored_memory.get("focus", "") or self._summary_field(project.summary, "Current focus:")
        recent_context = stored_memory.get("recent_context", "") or self._summary_field(project.summary, "Recent context:")
        lines = [
            "# PROJECT_SKILL",
            "",
            "## Startup",
            f"- First read `PROJECT_MEMORY.md` in `{project.workspace_path}`.",
            "- Treat `Current Focus` as the default priority unless the user changes direction.",
            "- Treat `Current State` as the latest known status, not as a full history replay.",
            "",
            "## Work Rules",
        ]
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
                "- Do not overwrite project memory with meta chat such as asking whether memory exists.",
                "- When the task meaningfully changes, update project memory through the shared sync flow.",
                "",
                "## Files",
                f"- Workspace: `{project.workspace_path}`",
                "- Durable state file: `PROJECT_MEMORY.md`",
                "- Instruction file: `PROJECT_SKILL.md`",
                "",
            ]
        )
        return "\n".join(lines)

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
        return written

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
