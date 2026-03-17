# Subagent Policy

Sub-agents exist for context isolation, not for simulating organization charts.

Spawn a sub-agent only when:

- the task is large enough to pollute the main Codex context
- a branch needs isolated exploration
- tests/docs/refactors need a separate execution lane

Current sub-agent labels are intentionally execution-oriented:

- `isolated-implementation`
- `isolated-test`
- `isolated-docs`

Rules:

- sub-agents are ephemeral
- sub-agents return results to the main worker
- main Codex remains the primary implementation agent
- sub-agents receive the same compact project memory snapshot as the main agent, not full long-form session history
