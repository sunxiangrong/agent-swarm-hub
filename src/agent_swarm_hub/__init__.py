"""Swarm coordination primitives for remote agent orchestration."""

from .adapter import AdapterResponse, CCConnectAdapter
from .escalation import EscalationDecision, EscalationPolicy
from .models import Event, EventType, Task, TaskStatus
from .remote import RemoteCommand, RemoteMessage, RemotePlatform, parse_remote_command
from .spokesperson import Spokesperson
from .swarm import SwarmCoordinator

__all__ = [
    "AdapterResponse",
    "CCConnectAdapter",
    "EscalationDecision",
    "EscalationPolicy",
    "Event",
    "EventType",
    "RemoteCommand",
    "RemoteMessage",
    "RemotePlatform",
    "Task",
    "TaskStatus",
    "Spokesperson",
    "SwarmCoordinator",
    "parse_remote_command",
]
