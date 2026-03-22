#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_swarm_hub.project_context import ProjectContextStore
from agent_swarm_hub.paths import project_session_db_path
from agent_swarm_hub.openviking_support import import_project_tree_to_openviking, sync_project_tree_to_openviking


def sync_project(project_id: str, *, push_live: bool = False, rebuild_tree: bool = False) -> bool:
    store = ProjectContextStore(project_session_db_path())
    store.sync_project_summary(project_id)
    store.sync_project_memory_file(project_id)
    store.sync_project_skill_file(project_id)
    python = sys.executable
    import subprocess

    subprocess.run(
        [
            python,
            str(REPO_ROOT / "scripts" / "build-openviking-project-tree.py"),
            "--project",
            project_id,
            *(["--rebuild"] if rebuild_tree else []),
        ],
        cwd=str(REPO_ROOT),
        check=True,
    )
    subprocess.run(
        [python, str(REPO_ROOT / "scripts" / "build-openviking-memory-bundle.py"), "--project", project_id],
        cwd=str(REPO_ROOT),
        check=True,
    )
    subprocess.run(
        [python, str(REPO_ROOT / "scripts" / "build-openviking-project-brain.py"), "--project", project_id],
        cwd=str(REPO_ROOT),
        check=True,
    )
    if not push_live:
        return False
    if rebuild_tree:
        return import_project_tree_to_openviking(project_id)
    return sync_project_tree_to_openviking(project_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync all recorded projects into OpenViking import trees.")
    parser.add_argument("--project", action="append", default=[], help="Optional specific project id(s) to sync.")
    parser.add_argument("--push-live", action="store_true", help="Also push each project tree into live OV resources.")
    parser.add_argument("--rebuild-tree", action="store_true", help="Rebuild the whole local/live project tree instead of updating the existing files in place.")
    args = parser.parse_args()

    store = ProjectContextStore(project_session_db_path())
    project_ids = args.project or [project.project_id for project in store.list_projects()]
    if not project_ids:
        print("No projects recorded.")
        return 0
    for project_id in project_ids:
        pushed = sync_project(project_id, push_live=args.push_live, rebuild_tree=args.rebuild_tree)
        suffix = " and pushed to live OV" if pushed else ""
        print(f"Synced OV project tree for `{project_id}`{suffix}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
