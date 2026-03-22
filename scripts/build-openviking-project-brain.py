#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


DEFAULT_IMPORT_ROOT = REPO_ROOT / "var" / "openviking" / "imports" / "projects"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _clean(text: str, limit: int = 500) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())[:limit]


def _extract_section(markdown: str, heading: str) -> str:
    pattern = rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, markdown, flags=re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def _parse_bullets(section: str, *, limit: int = 6, item_limit: int = 260) -> list[str]:
    items = [line[2:].strip() for line in section.splitlines() if line.startswith("- ")]
    return [_clean(item, item_limit) for item in items if item][:limit]


def _keyword_tokens(*texts: str) -> set[str]:
    tokens: set[str] = set()
    stopwords = {
        "project",
        "current",
        "state",
        "next",
        "step",
        "latest",
        "known",
        "focus",
        "summary",
        "workspace",
        "session",
        "runtime",
        "none",
        "recorded",
    }
    for text in texts:
        for token in re.findall(r"[A-Za-z0-9_+-]+|[\u4e00-\u9fff]{2,}", text or ""):
            lowered = token.lower()
            if len(lowered) < 2 or lowered in stopwords:
                continue
            tokens.add(lowered)
    return tokens


def _first_prefixed(items: list[str], prefix: str) -> str:
    for item in items:
        if item.startswith(prefix):
            return item.removeprefix(prefix).strip()
    return ""


def _is_low_value_progress(text: str) -> bool:
    lowered = (text or "").strip().lower()
    if not lowered:
        return True
    noisy_fragments = (
        "no active task yet",
        "send a normal message to start",
        "cache summary:",
        "recent:",
        "task:",
    )
    return any(fragment in lowered for fragment in noisy_fragments)


def _extract_runtime_recent(text: str) -> str:
    value = text or ""
    recent_match = re.search(r"Recent:\s*(.+?)(?:\s+Cache summary:|\s+State:|\Z)", value, flags=re.IGNORECASE)
    if recent_match:
        return _clean(recent_match.group(1), 260)
    state_match = re.search(r"State:\s*(.+?)(?:\s+Recent:|\s+Cache summary:|\Z)", value, flags=re.IGNORECASE)
    if state_match:
        return _clean(state_match.group(1), 260)
    return ""


def _extract_bound_field(item: str, field: str) -> str:
    match = re.search(rf"{re.escape(field)}=(.+?)(?: \| [a-z_]+=|\Z)", item or "", flags=re.IGNORECASE)
    if not match:
        return ""
    return _clean(match.group(1), 260)


def _is_noisy_bound_item(item: str) -> bool:
    lowered = (item or "").lower()
    noisy_fragments = ("tls handshake eof", "falling back from websockets", "stream disconnected")
    return any(fragment in lowered for fragment in noisy_fragments)


def _select_bound_progress(items: list[str], *, mission: str, current_state: str, next_step: str) -> str:
    noisy_fragments = ("tls handshake eof", "falling back from websockets", "stream disconnected")
    target_tokens = _keyword_tokens(mission, current_state, next_step)
    best_score = -1
    best_value = ""
    for item in items:
        summary = _extract_bound_field(item, "summary") or _extract_bound_field(item, "title")
        if not summary or _is_low_value_progress(summary):
            continue
        lowered = summary.lower()
        if any(fragment in lowered for fragment in noisy_fragments):
            continue
        score = len(_keyword_tokens(summary) & target_tokens)
        if score >= best_score:
            best_score = score
            best_value = summary
    return best_value


def _select_brain_decisions(items: list[str], *, mission: str, current_state: str, next_step: str) -> list[str]:
    if not items:
        return []
    noisy_fragments = (
        "斜杠命令",
        "slash command",
        "聊天框",
        "同行评审",
        "peer review",
        "task id:",
        "/help",
        "/plan",
        "/tp",
        "如何交互",
        "预定义技能",
        "常用斜杠命令",
    )
    preferred_terms = {"ov", "openviking", "dashboard", "tmux", "swarm", "memory"}
    target_tokens = _keyword_tokens(mission, current_state, next_step) | preferred_terms
    scored: list[tuple[int, int, str]] = []
    for index, item in enumerate(items):
        lowered = item.lower()
        if any(fragment in lowered for fragment in noisy_fragments):
            continue
        item_tokens = _keyword_tokens(item)
        overlap = len(item_tokens & target_tokens)
        score = overlap
        if any(term in lowered for term in preferred_terms):
            score += 1
        scored.append((score, index, item))
    if not scored:
        return []
    scored.sort(key=lambda row: (row[0], row[1]))
    selected = [item for score, _, item in scored if score > 0][-4:]
    return selected


def build_project_brain(project_id: str, import_root: Path) -> str:
    project_root = import_root / project_id
    memory_md = _read_text(project_root / "memory" / "PROJECT_MEMORY.md")
    skill_md = _read_text(project_root / "skills" / "PROJECT_SKILL.md")
    bundle_md = _read_text(project_root / "runtime" / "memory_bundle.md")

    mission = _clean(_extract_section(memory_md, "Current Focus"), 320) or "No current mission recorded."
    current_state = _clean(_extract_section(memory_md, "Current State"), 360)
    next_step = _clean(_extract_section(memory_md, "Next Step"), 280) or "No next best step recorded."
    key_rules = _parse_bullets(_extract_section(memory_md, "Key Rules"), limit=4, item_limit=220)
    work_rules = _parse_bullets(_extract_section(skill_md, "Work Rules"), limit=4, item_limit=220)
    memory_rules = _parse_bullets(_extract_section(skill_md, "Memory Rules"), limit=3, item_limit=220)
    stable_rules = []
    for item in key_rules + work_rules + memory_rules:
        if _is_low_value_progress(item):
            continue
        if item.lower().startswith("task:"):
            continue
        if item and item not in stable_rules:
            stable_rules.append(item)

    current_run = _parse_bullets(_extract_section(bundle_md, "Current Run"), limit=6, item_limit=260)
    recent_decisions = _parse_bullets(
        _extract_section(bundle_md, "Key Decisions From Recent Dialogue"),
        limit=5,
        item_limit=280,
    )
    bound_sessions = _parse_bullets(_extract_section(bundle_md, "Bound Session Snapshots"), limit=3, item_limit=260)
    recent_handoffs = _parse_bullets(_extract_section(bundle_md, "Recent Handoffs"), limit=3, item_limit=260)

    runtime_summary = _first_prefixed(current_run, "Runtime summary:")
    bound_summary = _select_bound_progress(
        bound_sessions,
        mission=mission,
        current_state=current_state,
        next_step=next_step,
    )
    runtime_recent = _extract_runtime_recent(runtime_summary)
    latest_progress_parts = []
    if current_state:
        latest_progress_parts.append(current_state)
    if runtime_recent and runtime_recent.lower() != current_state.lower():
        latest_progress_parts.append(runtime_recent)
    if bound_summary and not _is_low_value_progress(bound_summary) and bound_summary.lower() != current_state.lower():
        latest_progress_parts.append(bound_summary)
    if runtime_summary and not _is_low_value_progress(runtime_summary) and runtime_summary.lower() != current_state.lower():
        latest_progress_parts.append(runtime_summary)
    elif current_run:
        active_signal = _first_prefixed(current_run, "Active task:")
        if active_signal and active_signal != "none":
            latest_progress_parts.append(f"Active task: {active_signal}")
    latest_progress_parts = [item for item in latest_progress_parts if not _is_low_value_progress(item)]
    latest_progress = _clean(" ".join(latest_progress_parts), 420) or "No current progress summary recorded."

    constraints = []
    for item in current_run:
        if _is_low_value_progress(item):
            continue
        if item not in constraints:
            constraints.append(item)
    for item in recent_handoffs:
        if _is_low_value_progress(item):
            continue
        if item not in constraints:
            constraints.append(item)
    for item in bound_sessions:
        if _is_noisy_bound_item(item):
            continue
        if _is_low_value_progress(item):
            continue
        if item not in constraints:
            constraints.append(item)
    constraints = constraints[:4]
    recent_decisions = _select_brain_decisions(
        recent_decisions,
        mission=mission,
        current_state=current_state,
        next_step=next_step,
    )

    lines = [
        "# PROJECT_BRAIN",
        "",
        "## Role",
        "This file is a focused project-brain summary for OpenViking retrieval.",
        "Read it before broader runtime bundles when you need the shortest high-signal project context.",
        "",
        "## Current Mission",
        mission,
        "",
        "## Latest Progress",
        latest_progress,
        "",
        "## Architecture And Operating Decisions",
    ]
    if stable_rules:
        for item in stable_rules[:6]:
            lines.append(f"- {item}")
    else:
        lines.append("- No durable operating decisions recorded.")

    lines.extend(["", "## Current Constraints And Signals"])
    if constraints:
        for item in constraints:
            lines.append(f"- {item}")
    else:
        lines.append("- No active constraints or runtime signals recorded.")

    lines.extend(["", "## Next Best Step", next_step, "", "## Key Recent Decisions"])
    if recent_decisions:
        for item in recent_decisions:
            lines.append(f"- {item}")
    else:
        lines.append("- No recent dialogue decisions recorded.")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a focused OpenViking project-brain summary.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--import-root", default=str(DEFAULT_IMPORT_ROOT))
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    import_root = Path(args.import_root).expanduser().resolve()
    output = (
        Path(args.output).expanduser().resolve()
        if args.output
        else (import_root / args.project / "runtime" / "project_brain.md").resolve()
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_project_brain(args.project, import_root), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
