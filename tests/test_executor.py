from agent_swarm_hub import EchoExecutor


def test_echo_executor_returns_prompt() -> None:
    result = EchoExecutor().run("hello world")

    assert result.backend == "echo"
    assert result.output == "hello world"
