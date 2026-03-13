# Telegram Mapping

## Goal

Convert Telegram updates into the repository's neutral `RemoteMessage` format, then hand them to the runtime coordinator through `CCConnectAdapter`.

## Current Scope

Implemented:

- Telegram inbound update normalization
- outbound reply payload builder
- command contract for `/write`, `/status`, `/escalations`, `/help`

Deferred:

- real webhook or polling runner
- bot token wiring
- Telegram API send/retry logic
- group permission guidance

## Expected Flow

```text
Telegram update
  -> telegram_update_to_remote_message(...)
  -> CCConnectAdapter.handle_message(...)
  -> build_telegram_outbound(...)
  -> Telegram sendMessage
```

## What You Will Need To Provide Later

- bot token from BotFather
- whether testing starts in private chat or group
- target test chat id
- whether forum topics / threaded groups need support
