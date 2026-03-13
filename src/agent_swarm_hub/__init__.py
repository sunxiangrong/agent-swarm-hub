"""Swarm coordination primitives for remote agent orchestration."""

from .adapter import AdapterResponse, CCConnectAdapter
from .config import LarkConfig, RuntimeConfig, TelegramConfig
from .escalation import EscalationDecision, EscalationPolicy
from .lark import LarkOutboundMessage, build_lark_text_outbound, lark_event_to_remote_message
from .models import Event, EventType, Task, TaskStatus
from .remote import RemoteCommand, RemoteMessage, RemotePlatform, parse_remote_command
from .runner import ChannelDispatchResult, LarkRunner, TelegramRunner
from .spokesperson import Spokesperson
from .swarm import SwarmCoordinator
from .telegram import TelegramOutboundMessage, build_telegram_outbound, telegram_update_to_remote_message

__all__ = [
    "AdapterResponse",
    "CCConnectAdapter",
    "ChannelDispatchResult",
    "EscalationDecision",
    "EscalationPolicy",
    "Event",
    "EventType",
    "LarkConfig",
    "LarkOutboundMessage",
    "LarkRunner",
    "RemoteCommand",
    "RemoteMessage",
    "RemotePlatform",
    "RuntimeConfig",
    "Task",
    "TaskStatus",
    "Spokesperson",
    "SwarmCoordinator",
    "TelegramConfig",
    "TelegramOutboundMessage",
    "TelegramRunner",
    "build_lark_text_outbound",
    "build_telegram_outbound",
    "lark_event_to_remote_message",
    "parse_remote_command",
    "telegram_update_to_remote_message",
]
