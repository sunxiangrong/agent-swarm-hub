from __future__ import annotations

import os
from dataclasses import dataclass


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    default_parse_mode: str = ""
    webhook_url: str = ""
    polling_timeout_s: int = 30

    @classmethod
    def from_env(cls) -> "TelegramConfig":
        return cls(
            enabled=_env_flag("ASH_TELEGRAM_ENABLED"),
            bot_token=os.getenv("ASH_TELEGRAM_BOT_TOKEN", "").strip(),
            default_parse_mode=os.getenv("ASH_TELEGRAM_PARSE_MODE", "").strip(),
            webhook_url=os.getenv("ASH_TELEGRAM_WEBHOOK_URL", "").strip(),
            polling_timeout_s=int(os.getenv("ASH_TELEGRAM_POLL_TIMEOUT_S", "30").strip() or "30"),
        )


@dataclass(frozen=True, slots=True)
class LarkConfig:
    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    verify_token: str = ""
    encrypt_key: str = ""

    @classmethod
    def from_env(cls) -> "LarkConfig":
        return cls(
            enabled=_env_flag("ASH_LARK_ENABLED"),
            app_id=os.getenv("ASH_LARK_APP_ID", "").strip(),
            app_secret=os.getenv("ASH_LARK_APP_SECRET", "").strip(),
            verify_token=os.getenv("ASH_LARK_VERIFY_TOKEN", "").strip(),
            encrypt_key=os.getenv("ASH_LARK_ENCRYPT_KEY", "").strip(),
        )


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    telegram: TelegramConfig
    lark: LarkConfig

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        return cls(
            telegram=TelegramConfig.from_env(),
            lark=LarkConfig.from_env(),
        )
