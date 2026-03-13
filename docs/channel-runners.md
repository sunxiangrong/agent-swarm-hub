# Channel Runners

## Goal

Provide lightweight runner scaffolding that bridges platform payloads into the runtime coordinator without yet binding real network clients.

## Implemented

- `TelegramRunner.handle_update(update)`
- `LarkRunner.handle_event(event)`
- shared env-backed config models

## Current Contract

Each runner:

- normalizes the incoming platform payload
- calls `CCConnectAdapter`
- builds a platform-specific outbound payload
- returns a neutral dispatch result object

## Why This Step Exists

This locks the boundary between:

- platform transport
- runtime coordination
- future send/retry/network logic

Without forcing real credentials or SDK dependencies too early.

## What You Will Need Later

For Telegram:

- bot token
- whether webhook or polling is preferred

For Lark:

- app credentials
- callback verification settings
- whether to use bot mode or full app mode
