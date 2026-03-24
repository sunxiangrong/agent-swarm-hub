## agent-swarm-hub

Repository map for human and agent contributors. Start here, then read only the
next file that matches the task.

### Core principle

- Treat this repo as the source of truth for project/session/memory/runtime
  behavior.
- Prefer maps over manuals: this file stays short and points to the deeper
  source of truth.
- Keep mechanical boundaries stable. If you change cross-module ownership,
  update `docs/ARCHITECTURE.md` and the architecture tests in `tests/`.

### Where to read first

- Product and usage overview:
  - `README.md`
- Architectural boundaries and harness model:
  - `docs/ARCHITECTURE.md`
- Change history and implementation rationale:
  - `docs/开发日志.md`

### Main code ownership

- `src/agent_swarm_hub/cli.py`
  - top-level command router and compatibility wrappers only
- `src/agent_swarm_hub/workspace_ops.py`
  - project/workspace selection and project bootstrap
- `src/agent_swarm_hub/local_chat.py`
  - local chat loop and checkpoint/finalize behavior
- `src/agent_swarm_hub/native_entry.py`
  - native provider launch, resume, env injection, postrun reconciliation
- `src/agent_swarm_hub/cli_ops.py`
  - operational commands: OpenViking, project-sessions, runtime cleanup
- `src/agent_swarm_hub/project_context.py`
  - project memory, shared/global memory, exports, consolidation

### Memory model

- `project_memory`
  - project-local runtime memory
- `shared:<group>`
  - shared rules for a subset of projects
- `global`
  - rules for all projects
- exported views:
  - `PROJECT_MEMORY.md`
  - `PROJECT_SKILL.md`
  - `SHARED_MEMORY.md`

### Provider model

- The shared project harness is provider-agnostic.
- Provider-specific sessions are attached under the shared project layer.
- Current built-in providers include `claude` and `codex`, but project
  identity, memory, shared scopes, and exported views must not depend on any
  single provider.

### OpenViking

- OpenViking is a derived/exported view, not the primary write path.
- Sync/export behavior belongs in `cli_ops.py`, `project_context.py`, and
  `openviking_support.py`.

### Tests to keep green

- `tests/test_session_store_path.py`
  - memory, shared scopes, env injection
- `tests/test_cli.py`
  - routing and local-native compatibility
- `tests/test_harness_architecture.py`
  - architectural boundary enforcement

### When adding new features

- If it changes how a project is entered: check `workspace_ops.py` or
  `native_entry.py`
- If it changes memory or cross-project rules: check `project_context.py`
- If it changes OpenViking/runtime operations: check `cli_ops.py`
- If it changes top-level commands: keep `cli.py` thin
- If it is provider-specific, keep it below the shared project harness layer.
