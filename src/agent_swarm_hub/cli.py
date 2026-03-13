from __future__ import annotations

import argparse

from .config import RuntimeConfig, load_env_file
from .lark_ws_runner import LarkWebSocketRunner
from .telegram_polling import TelegramPollingRunner
from .telegram_service import TelegramService


def main() -> int:
    parser = argparse.ArgumentParser(description="agent-swarm-hub local runners")
    parser.add_argument(
        "--env-file",
        default=".env.local",
        help="Optional local env file to load before reading config",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    lark_ws = subparsers.add_parser("lark-ws", help="Start the Lark websocket event listener")
    lark_ws.add_argument(
        "--print-config",
        action="store_true",
        help="Print effective Lark config and exit instead of starting the client",
    )
    telegram_poll = subparsers.add_parser("telegram-poll", help="Run Telegram polling for personal local use")
    telegram_poll.add_argument("--once", action="store_true", help="Process one polling cycle and exit")
    telegram_poll.add_argument("--offset", type=int, default=None, help="Optional Telegram update offset")
    telegram_poll.add_argument(
        "--print-config",
        action="store_true",
        help="Print effective Telegram config and exit instead of polling",
    )

    args = parser.parse_args()
    load_env_file(args.env_file)

    if args.command == "lark-ws":
        config = RuntimeConfig.from_env().lark
        if args.print_config:
            print(
                {
                    "enabled": config.enabled,
                    "app_id": config.app_id,
                    "verify_token": config.verify_token,
                    "encrypt_key_configured": bool(config.encrypt_key),
                }
            )
            return 0

        runner = LarkWebSocketRunner.create(config)
        runner.start()
        return 0
    if args.command == "telegram-poll":
        config = RuntimeConfig.from_env().telegram
        if args.print_config:
            print(
                {
                    "enabled": config.enabled,
                    "bot_token_configured": bool(config.bot_token),
                    "polling_timeout_s": config.polling_timeout_s,
                    "parse_mode": config.default_parse_mode or None,
                }
            )
            return 0

        polling = TelegramPollingRunner(TelegramService(config))
        result = polling.run_once(offset=args.offset)
        print(
            {
                "updates_seen": result.updates_seen,
                "updates_processed": result.updates_processed,
                "next_offset": result.next_offset,
            }
        )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
