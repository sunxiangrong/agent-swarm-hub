from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path


DEFAULT_PROJECT_SESSION_DB = "/Users/sunxiangrong/Desktop/CLI/local-skills/project-session-manager/data/sessions.sqlite3"


@dataclass(frozen=True, slots=True)
class ProjectContext:
    project_id: str
    title: str
    workspace_path: str
    profile: str
    summary: str
    provider_session_count: int
    active_session_count: int = 0
    recent_messages: tuple[str, ...] = ()


class ProjectContextStore:
    def __init__(self, db_path: str | None = None):
        self.db_path = Path(db_path or os.getenv("ASH_PROJECT_SESSION_DB", "").strip() or DEFAULT_PROJECT_SESSION_DB)

    def get_for_workspace_path(self, workspace_path: str | None) -> ProjectContext | None:
        if not workspace_path or not self.db_path.exists():
            return None
        resolved = str(Path(workspace_path).expanduser().resolve())
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            provider_columns = {row["name"] for row in conn.execute("PRAGMA table_info(provider_sessions)").fetchall()}
            project_columns = {row["name"] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
            tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            active_expr = "SUM(CASE WHEN ps.status = 'active' THEN 1 ELSE 0 END)" if "status" in provider_columns else "COUNT(ps.raw_session_id)"
            profile_expr = "p.profile" if "profile" in project_columns else "'' AS profile"
            row = conn.execute(
                f"""
                SELECT p.project_id, p.title, p.workspace_path, {profile_expr}, p.summary,
                       COUNT(ps.raw_session_id) AS provider_session_count,
                       {active_expr} AS active_session_count
                FROM projects p
                LEFT JOIN provider_sessions ps ON ps.project_id = p.project_id
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
        lines = [
            f"Project: {project.project_id}",
            f"Workspace: {project.workspace_path}",
            f"Profile: {project.profile}",
            f"Summary: {project.summary}",
            f"Active Provider Sessions: {project.active_session_count}",
        ]
        if project.recent_messages:
            lines.append("Recent Project Messages:")
            lines.extend(f"- {message}" for message in project.recent_messages[-6:])
        return "\n".join(lines)
