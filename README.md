# agent-swarm-hub

Independent orchestration repository for a runtime-coordinated swarm workflow built around `ccb`, `tmux`, and remote chat entrypoints such as `cc-connect`, Lark, and Telegram.

## Scope

This repository owns:

- swarm coordination primitives
- runtime coordination policy
- escalation policy
- spokesperson summaries for remote chat tools
- integration glue for `ccb`, `cc-connect`, and optional `Superpowers`

This repository does not own:

- machine bootstrap and cross-device environment sync
- secrets or host-specific overrides
- vendored third-party source trees

Those remain in [`agent-env`](../agent-env).

## Model

Internal execution is a `swarm`: agents specialize, split work further inside their own domain, and raise events when blocked or when they disagree.

External presentation is a `single spokesperson`: remote tools should see only consolidated progress, escalations, and final summaries unless a child agent opinion is explicitly promoted.

The coordinator is a runtime layer, not a permanent "boss" agent. Claude Code is the default spokesperson and synthesizer, and can also participate as a high-capability node when needed.

See [docs/swarm-architecture.md](/Users/sunxiangrong/Desktop/CLI/git/agent-swarm-hub/docs/swarm-architecture.md).

## Layout

```text
docs/                 architecture and integration notes
src/agent_swarm_hub/  runtime coordinator implementation
tests/                focused behavior tests
scripts/              bootstrap helpers
config/               example routing and escalation config
```

## Superpowers Strategy

`Superpowers` is treated as an optional planning/review layer, not the runtime coordinator.

Recommended first-pass integrations:

- brainstorming
- writing-plans
- requesting-code-review
- systematic-debugging
- verification-before-completion

Deferred until needed:

- subagent-driven-development
- test-driven-development
- using-git-worktrees

See [docs/superpowers-integration.md](/Users/sunxiangrong/Desktop/CLI/git/agent-swarm-hub/docs/superpowers-integration.md).

## Development

Use the `cli` conda environment.

```bash
conda run -n cli pytest
```

## Next Steps

1. Add `cc-connect` adapters for Telegram and Lark.
2. Bind runtime coordinator actions to `ccb` execution backends.
3. Export install/bootstrap commands into `agent-env` once the interfaces settle.
