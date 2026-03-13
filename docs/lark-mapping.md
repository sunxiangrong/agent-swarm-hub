# Lark Mapping

## Goal

Convert Lark event payloads into the repository's neutral `RemoteMessage` format, then hand them to the runtime coordinator through `CCConnectAdapter`.

## Current Scope

Implemented:

- Lark inbound event normalization for text messages
- outbound text payload builder
- official SDK-backed message request builder
- command contract for `/write`, `/status`, `/escalations`, `/help`

Deferred:

- signature verification
- app credentials and tenant install wiring
- actual SDK send with your real app permissions

## Expected Flow

```text
Lark event callback
  -> LarkService.challenge_response(...) if challenge
  -> LarkRunner / LarkService
  -> build_lark_text_outbound(...)
  -> client.im.v1.message.create(...)
```

## What You Will Need To Provide Later

- app id and app secret or bot credentials
- whether you want custom bot or full app mode
- target chat for initial testing
- whether thread replies are required
