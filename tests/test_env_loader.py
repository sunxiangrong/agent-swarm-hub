import os
from pathlib import Path

from agent_swarm_hub import load_env_file


def test_load_env_file_reads_local_values(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "ASH_TELEGRAM_ENABLED=true\nASH_TELEGRAM_BOT_TOKEN='token-1'\n# comment\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("ASH_TELEGRAM_ENABLED", raising=False)
    monkeypatch.delenv("ASH_TELEGRAM_BOT_TOKEN", raising=False)

    loaded = load_env_file(env_file)

    assert loaded["ASH_TELEGRAM_ENABLED"] == "true"
    assert loaded["ASH_TELEGRAM_BOT_TOKEN"] == "token-1"


def test_load_env_file_respects_existing_env_by_default(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text("ASH_TELEGRAM_ENABLED=false\n", encoding="utf-8")
    monkeypatch.setenv("ASH_TELEGRAM_ENABLED", "true")

    load_env_file(env_file)

    assert os.environ["ASH_TELEGRAM_ENABLED"] == "true"
