from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_env_file(path: str | os.PathLike[str] = ".env.local", *, override: bool = False) -> dict[str, str]:
    env_path = Path(path)
    if not env_path.exists():
        return {}

    loaded: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value
        loaded[key] = value
    return loaded


def apply_runtime_env() -> None:
    proxy_url = os.getenv("ASH_PROXY_URL", "").strip()
    if not proxy_url:
        return
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        os.environ.setdefault(key, proxy_url)


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
