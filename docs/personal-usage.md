# Personal Usage

## Goal

Make the current Telegram and Lark integration easy to run locally for one person before moving to daemon mode.

## Local Secret File

Create a local `.env.local` in the repository root. It is ignored by git.

Example:

```bash
ASH_TELEGRAM_ENABLED=true
ASH_TELEGRAM_BOT_TOKEN=your-telegram-token
ASH_TELEGRAM_POLL_TIMEOUT_S=10

ASH_LARK_ENABLED=true
ASH_LARK_APP_ID=your-lark-app-id
ASH_LARK_APP_SECRET=your-lark-app-secret
ASH_LARK_VERIFY_TOKEN=your-lark-verify-token
```

## Telegram

Print config:

```bash
cd /Users/sunxiangrong/Desktop/CLI/git/agent-swarm-hub
PYTHONPATH=src conda run -n cli python -m agent_swarm_hub.cli telegram-poll --print-config
```

Run one polling cycle:

```bash
cd /Users/sunxiangrong/Desktop/CLI/git/agent-swarm-hub
PYTHONPATH=src conda run -n cli python -m agent_swarm_hub.cli telegram-poll --once
```

If your network needs a proxy:

```bash
export http_proxy=http://127.0.0.1:6789
export https_proxy=http://127.0.0.1:6789
```

## Lark

Print config:

```bash
cd /Users/sunxiangrong/Desktop/CLI/git/agent-swarm-hub
PYTHONPATH=src conda run -n cli python -m agent_swarm_hub.cli lark-ws --print-config
```

Start long connection:

```bash
cd /Users/sunxiangrong/Desktop/CLI/git/agent-swarm-hub
PYTHONPATH=src conda run -n cli python -m agent_swarm_hub.cli lark-ws
```

## When To Add A Daemon

You likely want daemon mode when:

- you do not want to keep terminals open
- you want automatic restart and reconnection
- you want persistent logs and background startup
- you want both Telegram and Lark always on
