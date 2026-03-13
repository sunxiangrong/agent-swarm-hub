# Integration Roadmap

## Phase 1

Repository bootstrap and core runtime-coordinated swarm primitives.

Delivered:

- task graph model
- structured event model
- escalation policy
- spokesperson summaries
- focused tests

## Phase 2

Remote channel adapters.

Target work:

- Telegram adapter using `cc-connect`
- Lark adapter using `cc-connect`
- remote command contract such as `/write`, `/status`, `/escalations`

## Phase 3

Execution binding.

Target work:

- map runtime coordinator actions to `ccb`
- bind task ids to `tmux` session lifecycle
- collect subagent outputs as structured swarm events

## Phase 4

Selective `Superpowers` integration.

Target work:

- pre-plan reasoning hooks
- review/debug/verification hooks
- selected skill manifest

## Phase 5

Promotion into `agent-env`.

Target work:

- bootstrap installer entry
- optional local override templates
- documented remote sync flow
