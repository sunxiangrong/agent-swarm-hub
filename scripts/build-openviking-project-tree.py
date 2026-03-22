#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_swarm_hub.project_context import ProjectContextStore
from agent_swarm_hub.paths import project_session_db_path


DEFAULT_TREE_ROOT = REPO_ROOT / "var" / "openviking" / "imports" / "projects"


def _copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def build_project_tree(project_id: str, tree_root: Path, *, rebuild: bool = False) -> Path:
    store = ProjectContextStore(project_session_db_path())
    project = store.get_project(project_id)
    if project is None:
        raise SystemExit(f"Unknown project: {project_id}")
    workspace = Path(project.workspace_path).expanduser() if (project.workspace_path or "").strip() else None

    project_root = tree_root / project_id
    if rebuild and project_root.exists():
        shutil.rmtree(project_root)
    (project_root / "memory").mkdir(parents=True, exist_ok=True)
    (project_root / "skills").mkdir(parents=True, exist_ok=True)
    (project_root / "runtime").mkdir(parents=True, exist_ok=True)
    (project_root / "dialogs").mkdir(parents=True, exist_ok=True)

    sources = {
        (workspace / "PROJECT_MEMORY.md") if workspace else Path(): project_root / "memory" / "PROJECT_MEMORY.md",
        (workspace / "PROJECT_SKILL.md") if workspace else Path(): project_root / "skills" / "PROJECT_SKILL.md",
    }
    for src, dest in sources.items():
        if src and src.exists():
            _copy(src, dest)

    readme = project_root / "README.md"
    readme.write_text(
        "\n".join(
            [
                f"# {project_id} OpenViking Project Tree",
                "",
                "This project-scoped tree is structured for OpenViking import.",
                "",
                "## Layout",
                "- `memory/`: exported local memory/cache views",
                "- `skills/`: exported local startup/rules views",
                "- `runtime/`: focused current-state bundle for retrieval",
                "- `dialogs/`: curated recent dialogue and decision bundle",
                "",
                "## Notes",
                "- The OpenViking project directory is the long-term project context store.",
                "- Local PROJECT_MEMORY.md and PROJECT_SKILL.md are exported local views.",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return project_root


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a project-scoped OpenViking import tree.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--tree-root", default=str(DEFAULT_TREE_ROOT))
    parser.add_argument("--rebuild", action="store_true", help="Remove and rebuild the full local import tree.")
    args = parser.parse_args()

    tree_root = Path(args.tree_root).expanduser().resolve()
    tree_root.mkdir(parents=True, exist_ok=True)
    project_root = build_project_tree(args.project, tree_root, rebuild=args.rebuild)
    print(project_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
