from agent_swarm_hub import Event, EventType, SwarmCoordinator


def test_split_task_creates_specialized_children() -> None:
    coordinator = SwarmCoordinator()
    state = coordinator.create_root_task(task_id="root", title="Prepare remote swarm architecture")

    event = coordinator.split_task(
        state,
        parent_id="root",
        children=[
            ("research", "Research chat adapters", "researcher"),
            ("plan", "Draft rollout plan", "planner"),
        ],
    )

    assert event.type is EventType.TASK_SPLIT
    assert state.tasks["research"].parent_id == "root"
    assert state.tasks["plan"].role == "planner"


def test_blocked_event_escalates() -> None:
    coordinator = SwarmCoordinator()
    state = coordinator.create_root_task(task_id="root", title="Implement swarm")
    coordinator.split_task(
        state,
        parent_id="root",
        children=[("build", "Build coordinator", "builder")],
    )

    decision = coordinator.record_event(
        state,
        Event(
            type=EventType.TASK_BLOCKED,
            task_id="build",
            role="builder",
            summary="Need webhook signing details from the platform adapter.",
        ),
    )

    assert decision.should_escalate is True
    assert decision.level == "BLOCKER"


def test_summary_prefers_remote_facing_progress() -> None:
    coordinator = SwarmCoordinator()
    state = coordinator.create_root_task(task_id="root", title="Remote agent rollout")
    coordinator.split_task(
        state,
        parent_id="root",
        children=[
            ("plan", "Draft task graph", "planner"),
            ("review", "Review escalation policy", "critic"),
        ],
    )
    coordinator.record_event(
        state,
        Event(
            type=EventType.TASK_STARTED,
            task_id="plan",
            role="planner",
            summary="Planner started shaping the initial task graph.",
        ),
    )
    coordinator.record_event(
        state,
        Event(
            type=EventType.TASK_COMPLETED,
            task_id="plan",
            role="planner",
            summary="Initial task graph is ready.",
        ),
    )

    summary = coordinator.render_remote_summary(state)

    assert "Task: Remote agent rollout" in summary
    assert "Recent: " in summary
    assert "Initial task graph is ready." in summary
