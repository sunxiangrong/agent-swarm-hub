from __future__ import annotations

import os
import shutil
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def cli_root() -> Path:
    return repo_root().parents[1]


def default_project_session_db() -> Path:
    return cli_root() / "local-skills" / "project-session-manager" / "data" / "sessions.sqlite3"


def default_projects_root() -> Path:
    return cli_root() / "projects"


def projects_root() -> Path:
    explicit = (os.getenv("ASH_PROJECTS_DIR") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return default_projects_root()


def project_session_db_path() -> Path:
    explicit = (os.getenv("ASH_PROJECT_SESSION_DB") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return default_project_session_db()


def default_ccb_lib_dir() -> Path:
    return cli_root() / "Codex" / "claude_code_bridge" / "lib"


def ccb_lib_dir() -> Path:
    explicit = (os.getenv("ASH_CCB_LIB_DIR") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return default_ccb_lib_dir()


def provider_command(provider: str) -> str:
    normalized = provider.strip().lower()
    env_key = f"ASH_{normalized.upper()}_BIN"
    explicit = (os.getenv(env_key) or "").strip()
    if explicit:
        return explicit

    local_wrapper = Path.home() / ".local" / "bin" / normalized
    if local_wrapper.exists():
        return str(local_wrapper)

    resolved = shutil.which(normalized)
    if resolved:
        return resolved

    return str(local_wrapper)
