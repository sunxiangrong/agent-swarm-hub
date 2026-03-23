from __future__ import annotations
"""Interactive local chat loop for project-bound shell conversations.

This module owns the REPL-style local chat flow, including memory checkpoints
and finalization. cli.py keeps a thin wrapper so existing entrypoints and tests
continue to call the same function names.
"""

import os
import sys
import time

from .adapter import CCConnectAdapter
from .executor import build_executor_for_config
from .remote import RemoteMessage, RemotePlatform
from .session_store import SessionStore


def local_message(*, chat_id: str, user_id: str, text: str) -> RemoteMessage:
    return RemoteMessage(
        platform=RemotePlatform.LOCAL,
        chat_id=chat_id,
        user_id=user_id,
        text=text,
    )


def consolidate_bound_workspace_memory(
    *,
    adapter: CCConnectAdapter,
    session_key: str,
    workspace_id: str,
    sync_project_memory_artifacts_cb,
) -> None:
    project_id = adapter._resolve_shared_project_id(workspace_id)  # type: ignore[attr-defined]
    if not project_id:
        return
    memory_key = adapter._memory_key(session_key, workspace_id)  # type: ignore[attr-defined]
    recent_rows = adapter.store.list_recent_messages(memory_key, limit=6)
    recent_messages = [f"{row['role']}: {row['text']}" for row in recent_rows if (row["text"] or "").strip()]
    workspace_session = adapter.store.get_workspace_session(session_key, workspace_id)
    live_summary = workspace_session.conversation_summary if workspace_session and workspace_session.conversation_summary else ""
    adapter.project_context_store.consolidate_project_memory(
        project_id,
        live_summary=live_summary,
        recent_messages=recent_messages,
    )
    sync_project_memory_artifacts_cb(adapter.project_context_store, project_id)


def checkpoint_local_chat_memory(
    *,
    adapter: CCConnectAdapter,
    chat_id: str,
    user_id: str,
    session_key: str,
    local_message_cb,
    consolidate_bound_workspace_memory_cb,
) -> None:
    workspace_id = adapter._get_bound_workspace(local_message_cb(chat_id=chat_id, user_id=user_id, text=""))  # type: ignore[attr-defined]
    if not workspace_id:
        return
    try:
        adapter._sync_project_memory(session_key=session_key, workspace_id=workspace_id)  # type: ignore[attr-defined]
        consolidate_bound_workspace_memory_cb(adapter=adapter, session_key=session_key, workspace_id=workspace_id)
    except Exception:
        return
    print(f"[agent-swarm-hub] memory checkpoint synced for `{workspace_id}`")


def finalize_local_chat_memory(
    *,
    adapter: CCConnectAdapter,
    chat_id: str,
    user_id: str,
    session_key: str,
    local_message_cb,
    consolidate_bound_workspace_memory_cb,
) -> None:
    workspace_id = adapter._get_bound_workspace(local_message_cb(chat_id=chat_id, user_id=user_id, text=""))  # type: ignore[attr-defined]
    if not workspace_id:
        return
    try:
        response = adapter.handle_message(local_message_cb(chat_id=chat_id, user_id=user_id, text="/quit"))
        print(response.text)
    except Exception:
        try:
            adapter._sync_project_memory(session_key=session_key, workspace_id=workspace_id)  # type: ignore[attr-defined]
            consolidate_bound_workspace_memory_cb(adapter=adapter, session_key=session_key, workspace_id=workspace_id)
            print(f"[agent-swarm-hub] finalized project memory for `{workspace_id}`")
        except Exception:
            return


def run_local_chat(
    *,
    provider: str,
    chat_id: str,
    user_id: str,
    project: str | None,
    local_message_cb,
    pick_startup_workspace_cb,
    auto_prepare_openviking_project_cb,
    finalize_local_chat_memory_cb,
    checkpoint_local_chat_memory_cb,
) -> int:
    store = SessionStore()
    adapter = CCConnectAdapter(
        executor=build_executor_for_config(
            mode=provider,
            transport="auto",
        ),
        store=store,
    )
    local_session_key = local_message_cb(chat_id=chat_id, user_id=user_id, text="").session_key
    checkpoint_interval = max(1, int((os.getenv("ASH_MEMORY_CHECKPOINT_INTERVAL") or "8").strip() or "8"))
    checkpoint_interval_s = max(30, int((os.getenv("ASH_MEMORY_CHECKPOINT_SECONDS") or "600").strip() or "600"))
    handled_messages = 0
    last_checkpoint_at = time.monotonic()
    if project:
        response = adapter.handle_message(local_message_cb(chat_id=chat_id, user_id=user_id, text=f"/use {project}"))
        print(response.text)
        auto_prepare_openviking_project_cb(project)
    elif sys.stdin.isatty():
        print(adapter.handle_message(local_message_cb(chat_id=chat_id, user_id=user_id, text="/projects")).text)
        selected_workspace = pick_startup_workspace_cb(store=store)
        if selected_workspace:
            response = adapter.handle_message(local_message_cb(chat_id=chat_id, user_id=user_id, text=f"/use {selected_workspace}"))
            print(response.text)
            auto_prepare_openviking_project_cb(selected_workspace)
        else:
            print("Temporary local chat is ready. Just start chatting.")
        print("Chat naturally. Use /help for advanced commands or /quit to exit the current project/chat.")
        print("Complex tasks will automatically enter planning / coordinated swarm execution when needed.")

    while True:
        try:
            line = input("> ")
        except KeyboardInterrupt:
            print()
            finalize_local_chat_memory_cb(adapter=adapter, chat_id=chat_id, user_id=user_id, session_key=local_session_key)
            return 130
        except EOFError:
            print()
            finalize_local_chat_memory_cb(adapter=adapter, chat_id=chat_id, user_id=user_id, session_key=local_session_key)
            return 0
        text = line.strip()
        if not text:
            continue
        if text in {"/quit", "/exit"}:
            response = adapter.handle_message(local_message_cb(chat_id=chat_id, user_id=user_id, text="/quit"))
            print(response.text)
            return 0
        response = adapter.handle_message(local_message_cb(chat_id=chat_id, user_id=user_id, text=text))
        print(response.text)
        handled_messages += 1
        now = time.monotonic()
        if handled_messages % checkpoint_interval == 0 or (now - last_checkpoint_at) >= checkpoint_interval_s:
            checkpoint_local_chat_memory_cb(adapter=adapter, chat_id=chat_id, user_id=user_id, session_key=local_session_key)
            last_checkpoint_at = now
