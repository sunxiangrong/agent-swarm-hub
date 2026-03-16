# Worker Flow

- `discussion`
  Claude discusses and clarifies the task.
- `planning`
  Claude produces `execution_plan` for medium/large work.
- `executing`
  Codex implements and self-validates.
- `verifying`
  Codex validates implementation in an isolated worktree.
- `reviewing`
  Claude reviews execution and verification output.
- `reported`
  Claude returns the final user-facing report.

Structured handoffs:

- `discussion_brief`
- `execution_plan`
- `execution_packet`
- `subagent_packet`
- `subagent_result`
- `verification_packet`
- `verification_result`
- `review_verdict`
