# ARCHITECTURE

This repository is a local, provider-agnostic project runtime.

Its job is not just to start models, but to control how projects, entry
surfaces, sessions, memory, shared rules, exports, transport, and cleanup fit
together.

In this repository, `runtime` and `harness` are complementary:

- `runtime`
  - how the system runs
- `harness`
  - how the system is organized, constrained, recovered, and observed

So `ash` should be read as both:

- a local project-agent runtime
- a project-level harness

## Harness model

```text
project identity
  -> memory orchestration
  -> session/runtime health
  -> transport substrate
  -> provider adapters
  -> exported views
  -> OpenViking sync/view
  -> runtime cleanup / recovery
```

The model is intentionally layered:

- `project`
  - the unit of work and entry
- `entry surface`
  - terminal, remote chat, dashboard, and future channels attached to the same
    runtime
- `session`
  - the provider-specific continuation handle
- `runtime health`
  - heartbeat, orphan detection, quarantine, repair/reset
- `transport`
  - the provider-neutral execution substrate; today this is primarily
    `ccb/askd` with `transport=auto`
  - future remote shell bridge surfaces should stay below the project harness
    and above raw pane primitives
- `provider`
  - a runtime-specific execution surface attached under the shared project
    harness
- `memory`
  - the runtime context attached to a project
- `shared/global`
  - the cross-project reusable rule layer
- `OpenViking`
  - derived/exported context view
- `runtime cleanup`
  - garbage collection for stale execution state

## Module ownership

The repository should be read as:

```text
shared project harness
  + provider adapters / sessions
```

This means project identity, memory, shared scopes, runtime health, exported
views, and OV integration are shared system concerns. Provider-specific logic
should stay in the provider/session layer and must not redefine the project
model.

### `src/agent_swarm_hub/cli.py`

Owns:

- top-level command parsing
- command dispatch
- thin compatibility wrappers for historical patch points

Must not become:

- the home of new business logic
- a large mixed implementation file

### `src/agent_swarm_hub/workspace_ops.py`

Owns:

- project picker
- workspace validation
- project bootstrap
- default shared-scope assignment for new projects

### `src/agent_swarm_hub/local_chat.py`

Owns:

- local chat loop
- checkpoint/finalize memory flow

### `src/agent_swarm_hub/native_entry.py`

Owns:

- `local-native` entry workflow
- provider resume/fresh launch
- provider-specific guardrails where needed
- entry-time heartbeat and unhealthy-resume fallback
- env injection for project/shared/global memory
- postrun session reconciliation

### `src/agent_swarm_hub/cli_ops.py`

Owns:

- project-session maintenance commands
- runtime health operations
- OpenViking operational commands
- runtime cleanup commands

### `src/agent_swarm_hub/auto_continue.py`

Owns:

- single-step automatic project continuation
- gating auto execution on project state + runtime health
- one-step execution followed by project-memory resync

### `src/agent_swarm_hub/runtime_monitor.py`

Owns:

- bounded monitor loops above runtime health
- repeated heartbeat probes with interval/cycle control
- optional repair/apply actions
- optional heartbeat-driven single-step auto-continue

### `src/agent_swarm_hub/runtime_health.py`

Owns:

- provider-process inspection
- unhealthy Codex process detection
- termination helpers used by heartbeat/quarantine flows

Phase 1 runtime-health completion criteria:

- entry-time heartbeat is automatic for project entry
- unhealthy resumed sessions are quarantined before reuse
- heartbeat state is projected back into project state
- dashboard/exported views surface runtime health
- explicit repair commands remain available for manual intervention

### `src/agent_swarm_hub/project_context.py`

Owns:

- `project_memory`
- `global_memory`
- `project_memory_scopes`
- consolidation and promotion
- exported markdown views

### Future remote bridge slice

The planned tmux + ssh bridge is not a generic tmux controller.

Its first phase should be read as:

- a project-scoped remote shell bridge
- built on top of tmux pane primitives
- constrained by pane/path/command policy

It should sit in this repository as a runtime surface under the shared project
harness, not as a replacement for the project model itself.

Under the current split, `ash` should not grow a full tmux primitive
implementation. The execution layer should live in the external
`tmux-bridge-mcp` fork, while `ash` keeps project boundary policy and a thin
integration surface.

The design target is documented in:

- `docs/remote-tmux-bridge.md`
- `src/agent_swarm_hub/bridge_policy.py`

## Dependency direction

The intended dependency direction is:

```text
cli.py
  -> workspace_ops.py
  -> local_chat.py
  -> native_entry.py
  -> cli_ops.py

workspace_ops.py
  -> project_context.py / session_store.py / paths.py

local_chat.py
  -> adapter/session/project helpers

native_entry.py
  -> project_context.py / session_store.py / paths.py / provider helpers / runtime_health.py

cli_ops.py
  -> project_context.py / runtime_health.py / runtime_cleanup.py / openviking_support.py / auto_continue.py

auto_continue.py
  -> adapter.py / executor.py / project_context.py / remote.py / session_store.py
```

Disallowed patterns:

- feature modules importing `cli.py`
- `workspace_ops.py` importing `native_entry.py`, `local_chat.py`, or
  `cli_ops.py`
- `native_entry.py` importing `cli.py`, `workspace_ops.py`, or `local_chat.py`
- `cli_ops.py` importing `cli.py`, `workspace_ops.py`, `local_chat.py`, or
  `native_entry.py`

## Mechanical enforcement

These architectural rules are not just documentation.

They are enforced by:

- `tests/test_harness_architecture.py`
- root `AGENTS.md`
- small-module discipline

When you intentionally change ownership boundaries:

1. update this file
2. update `AGENTS.md`
3. update the architecture tests

## Shared-memory hooks

Every project should be able to discover shared memory mechanically.

Current hooks:

- `PROJECT_MEMORY.md`
  - `## Shared Memory Hooks`
- `PROJECT_SKILL.md`
  - `## Shared Memory Hooks`
- native env vars:
  - `ASH_SHARED_MEMORY_SUMMARY`
  - `ASH_SHARED_MEMORY_HINTS`
  - `ASH_SHARED_MEMORY_SCOPES`
- compatibility env vars:
  - `ASH_GLOBAL_MEMORY_SUMMARY`
  - `ASH_GLOBAL_MEMORY_HINTS`

## Cross-agent CLI rule

All structural changes in this repository should default to cross-agent CLI
reuse.

Good shared layers:

- project identity
- project memory
- shared/global memory
- exported markdown views
- OpenViking sync/view
- runtime cleanup

Provider-specific layers:

- raw native session ids
- provider-specific resume conventions
- provider-only execution quirks

CCB-specific rule:

- treat `ccb/askd` as the default coordinated execution substrate
- use `transport=auto` as the shared project default unless a project
  explicitly requires `direct`

Runtime rule:

- treat `ash` as the local runtime above all providers and channels
- treat heartbeat, orphan detection, quarantine, and repair/reset as runtime
  concerns, not provider-private behavior
- do not let a provider define project identity or memory boundaries

Rule of thumb:

- if it is about the project, keep it provider-agnostic
- if it is about a concrete native runtime, keep it provider-specific

## Design bias

Prefer:

- repository as source of truth
- maps over giant instructions
- mechanical guardrails over tribal memory
- garbage collection over entropy accumulation

Avoid:

- unbounded growth in `cli.py`
- duplicating rules in many places
- mixing project state with shared/global rules
- treating derived markdown/OV views as the primary write source
- treating a single provider CLI as the primary system object
