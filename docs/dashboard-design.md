# Dashboard Design Rules

## Goal

Build `ash` as a high-density multi-project operations console, not a generic SaaS dashboard and not a marketing page.

## Fixed Design Prompt

Use this prompt as the default design direction when iterating on the dashboard:

```text
Design a high-density multi-project operations console for agent workflows.
Style: terminal-adjacent command center.
Use Lucide-style icons only.
Prioritize scanability, active sessions, runtime summaries, next steps, and project status.
Avoid generic SaaS dashboard patterns, decorative 3D effects, and heavy glassmorphism.
Motion should be minimal and functional.
```

## frontend-design Rules For ash

- Information density is more important than decorative whitespace.
- Every card should answer: what is active, what changed, what should I open next.
- Prefer stable layout and strong hierarchy over novelty.
- Use a restrained token system:
  - one background family
  - one panel family
  - one neutral text scale
  - one semantic status palette
- Motion is allowed only for:
  - enter transitions
  - hover emphasis
  - state changes
- Do not add decorative 3D, complex parallax, or scroll-showcase effects.
- Avoid UI that looks like a startup analytics template.

## Icon Standard

- Use Lucide semantics for icon choice and naming.
- Keep icons lightweight and status-oriented.
- Prefer icons for:
  - project
  - pin/watch
  - session
  - run/execute
  - warning/block
  - sync

## Phase 1 UI Priorities

- Pinned or currently active projects first
- Current focus
- Current state
- Next step
- Live summary
- Current bound sessions
- Session inventory

## Phase 2 UI Priorities

- Office-like spatial grouping
- Stronger state zoning
- tmux bridge visibility
- Session control actions
