from __future__ import annotations
"""Workspace and project selection helpers for interactive entry flows.

This module keeps the menu-driven project picker, workspace validation, and new
project bootstrap logic out of cli.py so the top-level command router stays
small and easier to review.
"""

import os
import sqlite3
from pathlib import Path

from .paths import project_session_db_path, projects_root
from .project_context import ProjectContextStore
from .session_store import SessionStore, WorkspaceRecord


ADD_PROJECT_SENTINEL = "__add_project__"


def resolve_workspace_selection(selection: str, workspaces: list[WorkspaceRecord]) -> str | None:
    raw = selection.strip()
    if not raw:
        return workspaces[0].workspace_id if workspaces else None
    if raw.lower() in {"add", "new", "add-project"}:
        return ADD_PROJECT_SENTINEL
    if raw.lower() in {"temporary", "temp"}:
        return None
    if raw.isdigit():
        index = int(raw) - 1
        if 0 <= index < len(workspaces):
            return workspaces[index].workspace_id
        if index == len(workspaces):
            return ADD_PROJECT_SENTINEL
        return ""
    normalized = raw.casefold()
    for workspace in workspaces:
        if normalized in {workspace.workspace_id.casefold(), workspace.title.casefold()}:
            return workspace.workspace_id
    return ""


def shared_projects_as_workspaces() -> list[WorkspaceRecord]:
    contexts = ProjectContextStore().list_projects()
    return [
        WorkspaceRecord(
            workspace_id=context.project_id,
            title=context.title,
            path=context.workspace_path,
            backend="claude",
            transport="direct",
            created_at="",
            updated_at="",
        )
        for context in contexts
    ]


def workspace_path_is_enterable(path: str | None) -> bool:
    if not (path or "").strip():
        return False
    try:
        return Path(path).expanduser().is_dir()
    except OSError:
        return False


def invocation_dir() -> Path:
    raw = (os.getenv("ASH_INVOKE_DIR") or os.getenv("PWD") or os.getcwd()).strip()
    try:
        return Path(raw).expanduser().resolve()
    except OSError:
        return Path.cwd()


def project_slug(title: str) -> str:
    project_id = title.strip().lower().replace(" ", "-")
    keep = [ch for ch in project_id if ch.isalnum() or ch in {"-", "_", "."}]
    return "".join(keep) or "default"


def new_project_workspace_path(title: str) -> Path:
    return (projects_root() / project_slug(title)).expanduser().resolve()


def upsert_project_workspace(*, store: SessionStore, title: str, workspace_path: Path) -> WorkspaceRecord:
    project_id = project_slug(title)
    db_path = project_session_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_path.mkdir(parents=True, exist_ok=True)
    summary = f"Project: {project_id}\nWorkspace: {workspace_path}"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
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
            )
            """
        )
        conn.execute(
            """
            INSERT INTO projects (project_id, title, workspace_path, profile, summary, created_at, updated_at)
            VALUES (?, ?, ?, '', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(project_id) DO UPDATE SET
                title=excluded.title,
                workspace_path=excluded.workspace_path,
                updated_at=CURRENT_TIMESTAMP
            """,
            (project_id, title.strip() or project_id, str(workspace_path), summary),
        )
        conn.commit()
    store.upsert_workspace(
        workspace_id=project_id,
        title=title.strip() or project_id,
        path=str(workspace_path),
        backend="claude",
        transport="direct",
    )
    context_store = ProjectContextStore(str(db_path))
    context_store.sync_project_memory_file(project_id)
    context_store.sync_project_skill_file(project_id)
    return store.get_workspace(project_id) or WorkspaceRecord(
        workspace_id=project_id,
        title=title.strip() or project_id,
        path=str(workspace_path),
        backend="claude",
        transport="direct",
        created_at="",
        updated_at="",
    )


def pick_startup_workspace(
    *,
    store: SessionStore,
    require_path: bool = False,
    shared_projects_as_workspaces_cb=shared_projects_as_workspaces,
    workspace_path_is_enterable_cb=workspace_path_is_enterable,
    upsert_project_workspace_cb=upsert_project_workspace,
    new_project_workspace_path_cb=new_project_workspace_path,
    resolve_workspace_selection_cb=resolve_workspace_selection,
) -> str | None:
    workspaces = shared_projects_as_workspaces_cb() or store.list_workspaces()
    if require_path:
        workspaces = [workspace for workspace in workspaces if workspace_path_is_enterable_cb(workspace.path)]
    if not workspaces:
        if require_path:
            print("No workspaces with an enterable path were found.")
            title = input("New project name (leave blank for temporary): ").strip()
            if not title:
                print("Temporary mode selected.")
                return None
            workspace = upsert_project_workspace_cb(
                store=store,
                title=title,
                workspace_path=new_project_workspace_path_cb(title),
            )
            print(f"Added project `{workspace.workspace_id}` at {workspace.path}.")
            return workspace.workspace_id
        print("No workspaces found. Temporary chat selected.")
        return None

    print("Choose a project or temporary chat:")
    for index, workspace in enumerate(workspaces, start=1):
        print(f"{index}. {workspace.workspace_id} ({workspace.backend}/{workspace.transport})")
    print(f"{len(workspaces) + 1}. add-project (create a new project directory)")
    print("Type temporary to start an unbound local chat.")

    prompt = "Select project by number/name, or type temporary"
    if workspaces:
        prompt += f" [Enter={workspaces[0].workspace_id}]"
    prompt += ": "

    while True:
        selection = input(prompt)
        resolved = resolve_workspace_selection_cb(selection, workspaces)
        if resolved == "":
            print("Unknown project selection. Choose an existing workspace or type temporary.")
            continue
        if resolved == ADD_PROJECT_SENTINEL:
            title = input("New project name: ").strip()
            if not title:
                print("Project name cannot be empty.")
                continue
            workspace = upsert_project_workspace_cb(
                store=store,
                title=title,
                workspace_path=new_project_workspace_path_cb(title),
            )
            print(f"Added project `{workspace.workspace_id}` at {workspace.path}.")
            return workspace.workspace_id
        if resolved is None:
            print("Temporary chat selected. Start chatting directly, or use /use <workspace> later to move into a project.")
            return None
        return resolved
