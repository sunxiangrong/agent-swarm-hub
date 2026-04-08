from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RemotePlatform(str, Enum):
    TELEGRAM = "telegram"
    LARK = "lark"
    LOCAL = "local"


@dataclass(frozen=True, slots=True)
class RemoteMessage:
    platform: RemotePlatform
    chat_id: str
    user_id: str
    text: str
    thread_id: str | None = None
    message_id: str | None = None

    @property
    def session_key(self) -> str:
        thread = self.thread_id or "root"
        return f"{self.platform.value}:{self.chat_id}:{thread}"


@dataclass(frozen=True, slots=True)
class RemoteCommand:
    name: str
    argument: str


def parse_remote_command(text: str) -> RemoteCommand:
    stripped = (text or "").strip()
    if not stripped:
        return RemoteCommand(name="help", argument="")
    if not stripped.startswith("/"):
        return RemoteCommand(name="write", argument=stripped)

    parts = stripped.split(maxsplit=1)
    command = parts[0][1:].lower()
    argument = parts[1].strip() if len(parts) > 1 else ""
    if command not in {"write", "execute", "new", "status", "sessions", "escalations", "projects", "use", "where", "project", "worker", "tasks", "autostep", "automonitor", "help", "confirm", "quit"}:
        return RemoteCommand(name="help", argument=stripped)
    return RemoteCommand(name=command, argument=argument)
