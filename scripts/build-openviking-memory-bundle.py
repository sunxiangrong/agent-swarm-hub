#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_swarm_hub.project_context import ProjectContextStore
from agent_swarm_hub.paths import project_session_db_path


DB_PATH = REPO_ROOT / "var" / "db" / "agent-swarm-hub.sqlite3"
DEFAULT_IMPORT_ROOT = REPO_ROOT / "var" / "openviking" / "imports" / "projects"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _extract_section(markdown: str, heading: str) -> str:
    pattern = rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, markdown, flags=re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def _clean(text: str, limit: int = 500) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())[:limit]


def _parse_bullets(section: str) -> list[str]:
    return [line[2:].strip() for line in section.splitlines() if line.startswith("- ")]


def _is_substantive_message(text: str) -> bool:
    cleaned = _clean(text, limit=900)
    if len(cleaned) < 20:
        return False
    noisy_fragments = (
        "/help",
        "/settings",
        "/clear",
        "你可以通过以下几种方式与我交互",
        "常用斜杠命令",
        "预定义技能",
    )
    return not any(fragment in cleaned for fragment in noisy_fragments)


def _select_key_messages(rows: list[tuple[str, str, str]], limit: int = 8) -> list[tuple[str, str, str]]:
    kept: list[tuple[str, str, str]] = []
    for role, text, created_at in reversed(rows):
        if not _is_substantive_message(text):
            continue
        kept.append((role, _clean(text, limit=700), created_at))
    return kept[-limit:]


def _load_runtime_snapshot(project_id: str) -> tuple[str, str, str, str, str, str]:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT workspace_id, phase, conversation_summary, claude_session_id, codex_session_id, active_task_id
            FROM workspace_sessions
            WHERE workspace_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
    return row or ("", "", "", "", "", "")


def _load_recent_messages(project_id: str) -> list[tuple[str, str, str]]:
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(
            """
            SELECT role, text, created_at
            FROM messages
            WHERE session_key LIKE ?
            ORDER BY id DESC
            LIMIT 30
            """,
            (f"%{project_id}%",),
        ).fetchall()


def _load_workspace_session_keys(project_id: str, *, limit: int = 4) -> list[str]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT session_key
            FROM workspace_sessions
            WHERE workspace_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (project_id, limit),
        ).fetchall()
    keys: list[str] = []
    for row in rows:
        session_key = str(row[0] or "").strip()
        if not session_key:
            continue
        keys.append(session_key)
        keys.append(f"{session_key}::{project_id}")
    seen: set[str] = set()
    deduped: list[str] = []
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        deduped.append(key)
    return deduped


def _load_bound_session_summaries(project_id: str) -> list[str]:
    store = ProjectContextStore(project_session_db_path())
    summaries: list[str] = []
    bindings = store.get_current_project_sessions(project_id)
    for provider in sorted(bindings):
        session_id = bindings[provider]
        rows = store.list_project_sessions(project_id, provider=provider, include_archived=True)
        for row in rows:
            if (row.get("session_id") or "").strip() != session_id:
                continue
            title = _clean(str(row.get("title") or ""), 220)
            summary = _clean(str(row.get("summary") or ""), 400)
            cwd = _clean(str(row.get("cwd") or ""), 140)
            parts = [f"{provider} session {session_id}"]
            if title:
                parts.append(f"title={title}")
            if summary:
                parts.append(f"summary={summary}")
            if cwd:
                parts.append(f"cwd={cwd}")
            summaries.append(" | ".join(parts))
            break
    return summaries


def _load_current_agent_cli_dialogue(project_id: str, *, limit: int = 12) -> list[tuple[str, str, str]]:
    session_keys = _load_workspace_session_keys(project_id)
    if not session_keys:
        return []
    placeholders = ",".join("?" for _ in session_keys)
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            f"""
            SELECT role, text, created_at
            FROM messages
            WHERE session_key IN ({placeholders})
            ORDER BY id DESC
            LIMIT 80
            """,
            session_keys,
        ).fetchall()
    return _select_key_messages(list(rows), limit=limit)


def _load_recent_handoffs(project_id: str) -> list[tuple[str, str, str, str, str]]:
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(
            """
            SELECT handoff_type, source_agent, target_agent, content_json, created_at
            FROM task_handoffs
            WHERE workspace_id = ?
            ORDER BY id DESC
            LIMIT 10
            """,
            (project_id,),
        ).fetchall()


def _load_project(project_id: str):
    store = ProjectContextStore(project_session_db_path())
    project = store.get_project(project_id)
    if project is None:
        raise SystemExit(f"Unknown project: {project_id}")
    return store, project


def build_bundle(project_id: str) -> str:
    store, project = _load_project(project_id)
    workspace = Path(project.workspace_path).expanduser() if (project.workspace_path or "").strip() else REPO_ROOT
    project_memory = workspace / "PROJECT_MEMORY.md"
    project_skill = workspace / "PROJECT_SKILL.md"
    memory_md = _read_text(project_memory)
    skill_md = _read_text(project_skill)
    snapshot = store.get_project_memory(project_id)

    current_focus = _extract_section(memory_md, "Current Focus") or snapshot.get("focus") or "No current focus recorded."
    current_state = _extract_section(memory_md, "Current State") or snapshot.get("current_state") or "No current state recorded."
    next_step = _extract_section(memory_md, "Next Step") or "No next step recorded."
    cache_summary = _clean(snapshot.get("memory", ""), 400) or "No cache summary recorded."
    key_rules = _parse_bullets(_extract_section(memory_md, "Key Rules"))
    work_rules = _parse_bullets(_extract_section(skill_md, "Work Rules"))
    memory_rules = _parse_bullets(_extract_section(skill_md, "Memory Rules"))

    workspace_id, phase, conversation_summary, claude_session_id, codex_session_id, active_task_id = _load_runtime_snapshot(project_id)
    messages = _load_current_agent_cli_dialogue(project_id)
    if not messages:
        messages = _select_key_messages(_load_recent_messages(project_id))
    bound_session_summaries = _load_bound_session_summaries(project_id)
    handoffs = list(reversed(_load_recent_handoffs(project_id)))
    sessions = store.get_current_project_sessions(project_id)

    lines: list[str] = [
        f"# {project_id} Project Memory Bundle",
        "",
        "## Project Summary",
        f"- Project: {project_id}",
        f"- Workspace: {project.workspace_path or ''}",
        f"- Focus: {_clean(current_focus, 300)}",
        f"- Current state: {_clean(current_state, 300)}",
        f"- Next step: {_clean(next_step, 300)}",
        f"- Cache summary: {cache_summary}",
        "",
        "## Stable Rules",
    ]
    stable_rules = key_rules[:4] + work_rules[:4] + memory_rules[:4]
    if stable_rules:
        for rule in stable_rules:
            if rule:
                lines.append(f"- {rule}")
    else:
        lines.append("- No stable rules recorded.")
    lines.extend(
        [
            "",
            "## Current Run",
            f"- Workspace session: {workspace_id or project_id}",
            f"- Phase: {phase or 'unknown'}",
            f"- Active task: {active_task_id or 'none'}",
            f"- Claude session: {claude_session_id or sessions.get('claude', 'none')}",
            f"- Codex session: {codex_session_id or sessions.get('codex', 'none')}",
            f"- Runtime summary: {_clean(conversation_summary, 700) or 'None'}",
            "",
            "## Key Decisions From Recent Dialogue",
        ]
    )
    if messages:
        for role, text, created_at in messages:
            lines.append(f"- [{created_at}] {role}: {text}")
    else:
        lines.append("- No substantive recent messages captured.")

    lines.extend(["", "## Bound Session Snapshots"])
    if bound_session_summaries:
        for item in bound_session_summaries:
            lines.append(f"- {item}")
    else:
        lines.append("- No bound provider session summaries recorded.")

    lines.extend(["", "## Recent Handoffs"])
    if handoffs:
        for handoff_type, source_agent, target_agent, content_json, created_at in handoffs:
            lines.append(
                f"- [{created_at}] {source_agent} -> {target_agent} ({handoff_type}): {_clean(content_json, 700)}"
            )
    else:
        lines.append("- No recent handoffs recorded.")

    lines.extend(
        [
            "",
            "## Source References",
            f"- Workspace memory view: {project_memory.name if project_memory.exists() else 'missing'}",
            f"- Workspace rules view: {project_skill.name if project_skill.exists() else 'missing'}",
            "- project_memory cache",
            "- workspace_sessions.conversation_summary",
            "- messages",
            "- task_handoffs",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a focused OpenViking memory bundle for a project.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    output = (
        Path(args.output).expanduser().resolve()
        if args.output
        else (DEFAULT_IMPORT_ROOT / args.project / "runtime" / "memory_bundle.md").resolve()
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    bundle = build_bundle(args.project)
    output.write_text(bundle, encoding="utf-8")
    dialogs_output = output.parent.parent / "dialogs" / "recent_dialogue.md"
    dialogs_output.parent.mkdir(parents=True, exist_ok=True)
    dialogs_output.write_text(bundle, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
