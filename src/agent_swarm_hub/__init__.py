"""Swarm coordination primitives for remote agent orchestration."""

from .adapter import AdapterResponse, CCConnectAdapter
from .config import LarkConfig, RuntimeConfig, TelegramConfig, apply_runtime_env, load_env_file
from .escalation import EscalationDecision, EscalationPolicy
from .executor import (
    AskExecutor,
    ClaudePrintExecutor,
    CodexExecExecutor,
    EchoExecutor,
    ExecutionResult,
    Executor,
    ExecutorError,
    FallbackExecutor,
    build_executor,
    build_executor_for_config,
)
from .lark import LarkOutboundMessage, build_lark_text_outbound, lark_event_to_remote_message
from .lark_service import LarkDispatch, LarkService
from .lark_ws_runner import LarkWebSocketRunner
from .models import Event, EventType, Task, TaskStatus
from .remote import RemoteCommand, RemoteMessage, RemotePlatform, parse_remote_command
from .session_store import (
    ChatBindingRecord,
    ChatSessionRecord,
    SessionStore,
    TaskRecord,
    WorkspaceRecord,
    WorkspaceSessionRecord,
)
from .runner import ChannelDispatchResult, LarkRunner, TelegramRunner
from .spokesperson import Spokesperson
from .swarm import SwarmCoordinator
from .telegram import TelegramOutboundMessage, build_telegram_outbound, telegram_update_to_remote_message
from .telegram_service import TelegramDispatch, TelegramService
from .telegram_transport import TelegramRequest, TelegramTransport
from .worker_session import ExecutorBusyError, LocalExecutorSession, LocalExecutorSessionPool

__all__ = [
    "AdapterResponse",
    "AskExecutor",
    "CCConnectAdapter",
    "ChannelDispatchResult",
    "ClaudePrintExecutor",
    "CodexExecExecutor",
    "EchoExecutor",
    "EscalationDecision",
    "EscalationPolicy",
    "Event",
    "EventType",
    "ExecutionResult",
    "ExecutorBusyError",
    "Executor",
    "ExecutorError",
    "FallbackExecutor",
    "LarkConfig",
    "LarkDispatch",
    "LarkOutboundMessage",
    "LarkRunner",
    "LarkService",
    "LarkWebSocketRunner",
    "LocalExecutorSession",
    "LocalExecutorSessionPool",
    "apply_runtime_env",
    "load_env_file",
    "RemoteCommand",
    "RemoteMessage",
    "RemotePlatform",
    "RuntimeConfig",
    "ChatSessionRecord",
    "ChatBindingRecord",
    "SessionStore",
    "Task",
    "TaskStatus",
    "TaskRecord",
    "WorkspaceRecord",
    "WorkspaceSessionRecord",
    "TelegramDispatch",
    "Spokesperson",
    "SwarmCoordinator",
    "TelegramConfig",
    "TelegramOutboundMessage",
    "TelegramRunner",
    "TelegramRequest",
    "TelegramService",
    "TelegramTransport",
    "build_lark_text_outbound",
    "build_executor",
    "build_executor_for_config",
    "build_telegram_outbound",
    "lark_event_to_remote_message",
    "parse_remote_command",
    "telegram_update_to_remote_message",
]
