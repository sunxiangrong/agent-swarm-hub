# Lark Mapping

## Goal

Convert Lark event payloads into the repository's neutral `RemoteMessage` format, then hand them to the runtime coordinator through `CCConnectAdapter`.

## Current Scope

Implemented:

- Lark inbound event normalization for text messages
- outbound text payload builder
- command contract for `/write`, `/status`, `/escalations`, `/help`

Deferred:

- signature verification
- challenge response handling
- app credentials and tenant install wiring
- message send/retry logic

## Expected Flow

```text
Lark event callback
  -> lark_event_to_remote_message(...)
  -> CCConnectAdapter.handle_message(...)
  -> build_lark_text_outbound(...)
  -> Lark send message API
```

## What You Will Need To Provide Later

- app id and app secret or bot credentials
- whether you want custom bot or full app mode
- target chat for initial testing
- whether thread replies are required
