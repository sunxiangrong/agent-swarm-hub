import os
from pathlib import Path

from agent_swarm_hub import apply_runtime_env, load_env_file


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


def test_apply_runtime_env_maps_proxy_url(monkeypatch) -> None:
    monkeypatch.setenv("ASH_PROXY_URL", "http://127.0.0.1:6789")
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(key, raising=False)

    apply_runtime_env()

    assert os.environ["http_proxy"] == "http://127.0.0.1:6789"
    assert os.environ["https_proxy"] == "http://127.0.0.1:6789"
    assert os.environ["HTTP_PROXY"] == "http://127.0.0.1:6789"
    assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:6789"
    assert os.environ["all_proxy"] == "http://127.0.0.1:6789"
    assert os.environ["ALL_PROXY"] == "http://127.0.0.1:6789"
