# ARCHITECTURE

This repository is a project-level agent harness. Its job is not just to start
models, but to control how projects, sessions, memory, shared rules, exports,
and cleanup fit together.

## Harness model

```text
project identity
  -> session binding
  -> project memory
  -> shared/group memory
  -> global memory
  -> exported views
  -> OpenViking sync/view
  -> runtime cleanup
```

The model is intentionally layered:

- `project`
  - the unit of work and entry
- `session`
  - the provider-specific continuation handle
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

This means project identity, memory, shared scopes, exported views, and OV
integration are shared system concerns. Provider-specific logic should stay in
the provider/session layer and must not redefine the project model.

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
- env injection for project/shared/global memory
- postrun session reconciliation

### `src/agent_swarm_hub/cli_ops.py`

Owns:

- project-session maintenance commands
- OpenViking operational commands
- runtime cleanup commands

### `src/agent_swarm_hub/project_context.py`

Owns:

- `project_memory`
- `global_memory`
- `project_memory_scopes`
- consolidation and promotion
- exported markdown views

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
  -> project_context.py / session_store.py / paths.py / provider helpers

cli_ops.py
  -> project_context.py / runtime_cleanup.py / openviking_support.py
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
