# Remote Shell

The remote chat shell and local swarm shell should share the same command logic.

Core commands:

- `/projects`
- `/use <workspace>`
- `/where`
- `/write <task>`
- `/execute [notes]`
- `/worker`
- `/tasks`
- `/sessions`
- `/help`

Design rule:

- chat shell behavior should stay consistent across local and remote entry points
- native `claude` / `codex` CLI behavior is separate from the shell command layer
