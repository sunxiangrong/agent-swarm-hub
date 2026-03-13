from __future__ import annotations

import argparse

from .config import RuntimeConfig
from .lark_ws_runner import LarkWebSocketRunner


def main() -> int:
    parser = argparse.ArgumentParser(description="agent-swarm-hub local runners")
    subparsers = parser.add_subparsers(dest="command", required=True)

    lark_ws = subparsers.add_parser("lark-ws", help="Start the Lark websocket event listener")
    lark_ws.add_argument(
        "--print-config",
        action="store_true",
        help="Print effective Lark config and exit instead of starting the client",
    )

    args = parser.parse_args()

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

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
