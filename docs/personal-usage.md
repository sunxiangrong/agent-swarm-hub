# Personal Usage

## Goal

Make the current Telegram and Lark integration easy to run locally for one person before moving to daemon mode.

## Local Secret File

Create a local `.env.local` in the repository root. It is ignored by git.

You can start from:

```bash
cp .env.local.example .env.local
```

Example:

```bash
ASH_EXECUTOR=codex
ASH_EXECUTOR_TIMEOUT_S=120

ASH_TELEGRAM_ENABLED=true
ASH_TELEGRAM_BOT_TOKEN=your-telegram-token
ASH_TELEGRAM_POLL_TIMEOUT_S=10
ASH_TELEGRAM_PARSE_MODE=
ASH_PROXY_URL=http://127.0.0.1:6789

ASH_LARK_ENABLED=true
ASH_LARK_APP_ID=your-lark-app-id
ASH_LARK_APP_SECRET=your-lark-app-secret
ASH_LARK_VERIFY_TOKEN=your-lark-verify-token
ASH_LARK_ENCRYPT_KEY=
```

## Telegram

Simplest startup:

```bash
./scripts/start-telegram.sh
```

Print config:

```bash
cd /Users/sunxiangrong/dev/cli/git/agent-swarm-hub
PYTHONPATH=src conda run -n cli python -m agent_swarm_hub.cli telegram-poll --print-config
```

Run one polling cycle:

```bash
cd /Users/sunxiangrong/dev/cli/git/agent-swarm-hub
PYTHONPATH=src conda run -n cli python -m agent_swarm_hub.cli telegram-poll --once
```

If your network needs a proxy:

```bash
ASH_PROXY_URL=http://127.0.0.1:6789
```

## Lark

Simplest startup:

```bash
./scripts/start-lark.sh
```

Print config:

```bash
cd /Users/sunxiangrong/dev/cli/git/agent-swarm-hub
PYTHONPATH=src conda run -n cli python -m agent_swarm_hub.cli lark-ws --print-config
```

Start long connection:

```bash
./scripts/start-lark.sh
```

## Both Together

Start the Lark listener and run one Telegram polling cycle:

```bash
./scripts/start-local.sh
```

## Native Project Chat

Use `ash-chat` for project-scoped native Claude or Codex sessions. Prefer this entry over invoking `local-native` by hand unless you are debugging the CLI itself.

Start with project selection:

```bash
./scripts/start-chat.sh codex
./scripts/start-chat.sh claude
```

Enter a specific project directly:

```bash
./scripts/start-chat.sh codex agent-swarm-hub
./scripts/start-chat.sh claude agent-browser
```

Debug-only raw CLI:

```bash
cd /Users/sunxiangrong/dev/cli/git/agent-swarm-hub
PYTHONPATH=src conda run -n cli python -m agent_swarm_hub.cli local-native --provider codex --project agent-swarm-hub
```

Current native project flow:

- `ash-chat` selects a project first, not a raw session
- before entering native CLI, it loads the project path and compact project memory
- if the project already has a current bound provider session, it resumes that session
- otherwise it starts a fresh native session in the correct project path
- after native CLI exits, it writes back project memory and refreshes project summary files
- if a new session becomes the current binding, older sessions for the same project and provider are archived automatically

Project memory files:

- `<project>/PROJECT_MEMORY.md`: durable project memory snapshot
- `<project>/PROJECT_SKILL.md`: startup and memory handling rules for the project

Manual maintenance commands:

```bash
cd /Users/sunxiangrong/dev/cli/git/agent-swarm-hub
PYTHONPATH=src conda run -n cli python -m agent_swarm_hub.cli project-sessions current agent-swarm-hub
PYTHONPATH=src conda run -n cli python -m agent_swarm_hub.cli project-sessions list agent-swarm-hub
PYTHONPATH=src conda run -n cli python -m agent_swarm_hub.cli project-sessions use agent-swarm-hub codex <session-id>
PYTHONPATH=src conda run -n cli python -m agent_swarm_hub.cli project-sessions sync-memory agent-swarm-hub
PYTHONPATH=src conda run -n cli python -m agent_swarm_hub.cli project-sessions sync-memory --all
```

When to use them:

- `current`: show the current bound native session per provider
- `list`: inspect active and archived native sessions recorded for a project
- `use`: switch the current binding to another recorded session and refresh summary files
- `sync-memory`: rebuild `projects.summary`, `PROJECT_MEMORY.md`, and `PROJECT_SKILL.md`

What the startup summary means:

- `Current Focus`: stable task direction
- `Current State`: latest useful progress, not just the last utterance
- `Next Step`: only shown when it adds information beyond focus and state
- `Project Memory`: compact long-term reminder when that is more useful than repeating the last hint

What you usually do not need:

- you usually do not need to call `local-native` directly
- you usually do not need to manually edit `PROJECT_MEMORY.md` or `PROJECT_SKILL.md`
- you usually do not need to run `sync-memory` after normal native usage, because `ash-chat` and `project-sessions use` already refresh memory automatically

## When To Add A Daemon

You likely want daemon mode when:

- you do not want to keep terminals open
- you want automatic restart and reconnection
- you want persistent logs and background startup
- you want both Telegram and Lark always on

## Personal Default Recommendation

For your current setup, the simplest personal default is:

```bash
ASH_EXECUTOR=codex
ASH_PROXY_URL=http://127.0.0.1:6789
```

That keeps Telegram API access and Codex execution on the same local proxy path.
