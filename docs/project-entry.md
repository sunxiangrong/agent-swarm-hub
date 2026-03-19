# Project Entry

- `ash-chat` is the project entry for native Claude/Codex CLI.
- Selecting a project in `ash-chat` should first load compact project memory and the workspace path.
- After project selection, `ash-chat` enters the target native CLI in that workspace.
- Session resume is secondary:
  - if a current provider binding exists, resume it
  - otherwise start a fresh native session in the correct project path
- Leaving native CLI should write back compact project memory and refresh project summary files.
- Rebinding to a new current native session should archive older sessions for the same project and provider.
- `ash-swarm` is the project entry for the local swarm shell.
- The local swarm shell and remote chat command layer should stay aligned.
- Entering `temporary` means:
  - no long-term project memory
  - no project binding
  - ephemeral context only
  - cleanup when leaving temporary mode

## Current Model

- `project` is the primary container
- `project memory` is compact and independent from any single raw session
- `session` is only a resumable window, not the primary memory carrier
- main agent and sub-agents both receive the same compact project memory snapshot

## Practical Rule

- Prefer `ash-chat` for native project work.
- Use `project-sessions use` only when you intentionally want to switch the current bound native session.
- Use `project-sessions sync-memory` only for repair or bulk cleanup, not as a normal step in daily usage.
