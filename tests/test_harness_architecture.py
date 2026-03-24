from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "agent_swarm_hub"


def _module_imports(module_name: str) -> set[str]:
    path = SRC / f"{module_name}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level and node.module:
                imports.add(node.module)
            elif node.level and node.module is None:
                for alias in node.names:
                    imports.add(alias.name)
            elif node.module and node.module.startswith("agent_swarm_hub."):
                imports.add(node.module.split(".", 1)[1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("agent_swarm_hub."):
                    imports.add(alias.name.split(".", 1)[1])
    return imports


def test_root_agents_map_exists_and_stays_short() -> None:
    agents = ROOT / "AGENTS.md"
    assert agents.exists()
    lines = agents.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 90, "AGENTS.md should stay a short map, not a manual."


def test_architecture_doc_exists() -> None:
    architecture = ROOT / "docs" / "ARCHITECTURE.md"
    assert architecture.exists()
    text = architecture.read_text(encoding="utf-8")
    assert "Module ownership" in text
    assert "Mechanical enforcement" in text


def test_workspace_ops_does_not_depend_on_runtime_entry_modules() -> None:
    imports = _module_imports("workspace_ops")
    assert "cli" not in imports
    assert "native_entry" not in imports
    assert "local_chat" not in imports
    assert "cli_ops" not in imports


def test_native_entry_stays_isolated_from_router_and_peer_workflows() -> None:
    imports = _module_imports("native_entry")
    assert "cli" not in imports
    assert "workspace_ops" not in imports
    assert "local_chat" not in imports
    assert "cli_ops" not in imports


def test_cli_ops_does_not_reach_back_into_router_or_entry_modules() -> None:
    imports = _module_imports("cli_ops")
    assert "cli" not in imports
    assert "workspace_ops" not in imports
    assert "local_chat" not in imports
    assert "native_entry" not in imports


def test_cli_router_imports_split_workflow_modules() -> None:
    imports = _module_imports("cli")
    assert {"workspace_ops", "local_chat", "native_entry", "cli_ops"} <= imports
