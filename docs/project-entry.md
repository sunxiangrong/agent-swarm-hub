# Project Entry

- `ash-chat` is the project entry for native Claude/Codex CLI.
- Selecting a project in `ash-chat` should first load compact project memory and the workspace path.
- After project selection, `ash-chat` enters the target native CLI in that workspace.
- Session resume is secondary:
  - if a matching provider session exists, resume it
  - otherwise start a fresh native session in the correct project path
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
