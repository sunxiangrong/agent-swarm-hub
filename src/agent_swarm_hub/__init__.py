"""Swarm coordination primitives for remote agent orchestration."""

from .adapter import AdapterResponse, CCConnectAdapter
from .config import LarkConfig, RuntimeConfig, TelegramConfig, apply_runtime_env, load_env_file
from .dashboard import build_dashboard_snapshot, serve_dashboard
from .escalation import EscalationDecision, EscalationPolicy
from .executor import (
    AskExecutor,
    ClaudePrintExecutor,
    ConfirmationRequiredError,
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


def __getattr__(name: str):
    if name in {"LarkDispatch", "LarkService"}:
        from .lark_service import LarkDispatch, LarkService

        globals()["LarkDispatch"] = LarkDispatch
        globals()["LarkService"] = LarkService
        return globals()[name]
    if name == "LarkWebSocketRunner":
        from .lark_ws_runner import LarkWebSocketRunner

        globals()["LarkWebSocketRunner"] = LarkWebSocketRunner
        return LarkWebSocketRunner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "AdapterResponse",
    "AskExecutor",
    "CCConnectAdapter",
    "ChannelDispatchResult",
    "ClaudePrintExecutor",
    "ConfirmationRequiredError",
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
    "build_dashboard_snapshot",
    "build_telegram_outbound",
    "lark_event_to_remote_message",
    "parse_remote_command",
    "serve_dashboard",
    "telegram_update_to_remote_message",
]
