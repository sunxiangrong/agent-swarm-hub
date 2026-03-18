# Lark Long Connection

## Why This Mode

Lark requires a live SDK websocket connection before the platform will save long-connection mode.

This repository now exposes a local startup entrypoint for that flow.

## Required Environment Variables

Set these in your local shell only:

```bash
export ASH_LARK_ENABLED=true
export ASH_LARK_APP_ID='your app id'
export ASH_LARK_APP_SECRET='your app secret'
export ASH_LARK_VERIFY_TOKEN='your verification token'
```

`ASH_LARK_ENCRYPT_KEY` can stay empty for the first pass if encryption is not enabled.

## Verify Effective Config

```bash
cd /Users/sunxiangrong/dev/cli/git/agent-swarm-hub
PYTHONPATH=src conda run -n cli python -m agent_swarm_hub.cli lark-ws --print-config
```

## Start the Local WebSocket Client

```bash
cd /Users/sunxiangrong/dev/cli/git/agent-swarm-hub
PYTHONPATH=src conda run -n cli python -m agent_swarm_hub.cli lark-ws
```

Once the client is connected:

1. return to the Lark developer console
2. save the long-connection mode
3. add `im.message.receive_v1`
4. add the bot to a test chat

## Current Runtime Behavior

On `im.message.receive_v1`:

- the event is normalized into `RemoteMessage`
- the runtime coordinator produces a response
- the service sends the response back through the official SDK

This first pass keeps processing lightweight so it stays inside the websocket timeout budget.
