from agent_swarm_hub import (
    CCConnectAdapter,
    EchoExecutor,
    Event,
    EventType,
    RemoteMessage,
    RemotePlatform,
    parse_remote_command,
)


def test_parse_remote_command_defaults_plain_text_to_write() -> None:
    command = parse_remote_command("Draft a rollout plan")

    assert command.name == "write"
    assert command.argument == "Draft a rollout plan"


def test_write_creates_session_and_status_reads_it() -> None:
    adapter = CCConnectAdapter(executor=EchoExecutor())
    message = RemoteMessage(
        platform=RemotePlatform.TELEGRAM,
        chat_id="chat-1",
        user_id="user-1",
        text="/write Draft a Telegram rollout",
    )

    write_response = adapter.handle_message(message)
    status_response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.TELEGRAM,
            chat_id="chat-1",
            user_id="user-1",
            text="/status",
        )
    )

    assert write_response.task_id is not None
    assert "Backend: echo" in write_response.text
    assert write_response.task_id in status_response.text


def test_blocker_event_becomes_visible_escalation() -> None:
    adapter = CCConnectAdapter(executor=EchoExecutor())
    base_message = RemoteMessage(
        platform=RemotePlatform.LARK,
        chat_id="chat-2",
        user_id="user-9",
        text="/write Prepare Lark adapter",
    )
    write_response = adapter.handle_message(base_message)
    task_id = write_response.task_id
    assert task_id is not None

    event_response = adapter.publish_event(
        RemoteMessage(
            platform=RemotePlatform.LARK,
            chat_id="chat-2",
            user_id="user-9",
            text="/status",
        ),
        Event(
            type=EventType.NEED_INPUT,
            task_id=task_id,
            role="planner",
            summary="Need bot permission scope confirmation.",
        ),
    )
    escalations_response = adapter.handle_message(
        RemoteMessage(
            platform=RemotePlatform.LARK,
            chat_id="chat-2",
            user_id="user-9",
            text="/escalations",
        )
    )

    assert event_response.escalation is not None
    assert event_response.escalation.should_escalate is True
    assert "Need bot permission scope confirmation." in escalations_response.text
