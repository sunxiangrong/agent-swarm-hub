# Telegram Mapping

## Goal

Convert Telegram updates into the repository's neutral `RemoteMessage` format, then hand them to the runtime coordinator through `CCConnectAdapter`.

## Current Scope

Implemented:

- Telegram inbound update normalization
- outbound reply payload builder
- Bot API request builders for `sendMessage`, `getUpdates`, and `setWebhook`
- command contract for `/write`, `/status`, `/escalations`, `/help`

Deferred:

- actual HTTP sending and retry loop
- group permission guidance

## Expected Flow

```text
Telegram update
  -> telegram_update_to_remote_message(...)
  -> TelegramRunner / TelegramService
  -> build_telegram_outbound(...)
  -> TelegramTransport.build_send_message(...)
  -> Telegram sendMessage
```

## What You Will Need To Provide Later

- bot token from BotFather
- webhook URL if you want webhook mode
- whether testing starts in private chat or group
- target test chat id
- whether forum topics / threaded groups need support
