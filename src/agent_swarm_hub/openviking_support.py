from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


DEFAULT_VLM_MODEL = "doubao-seed-2-0-pro-260215"
DEFAULT_EMBEDDING_MODEL = "doubao-embedding-vision-251215"
DEFAULT_EMBEDDING_DIMENSION = 1024
DEFAULT_EMBEDDING_INPUT = "multimodal"
DEFAULT_SERVER_HOST = "127.0.0.1"
DEFAULT_SERVER_PORT = 1933
DEFAULT_OVERVIEW_TIMEOUT = 1.0
DEFAULT_STORAGE_WORKSPACE = str(
    (Path(__file__).resolve().parents[2] / "var" / "openviking" / "data").resolve()
)
DEFAULT_CONFIG_PATH = (Path(__file__).resolve().parents[2] / "var" / "openviking" / "ov.conf").resolve()
DEFAULT_IMPORT_TREE_ROOT = (Path(__file__).resolve().parents[2] / "var" / "openviking" / "imports" / "projects").resolve()


def _require_secret(env: dict[str, str], primary: str, fallback: str | None = None) -> str:
    value = env.get(primary)
    if value:
        return value
    if fallback:
        value = env.get(fallback)
        if value:
            return value
    names = [primary]
    if fallback:
        names.append(fallback)
    joined = " or ".join(names)
    raise ValueError(f"Missing required environment variable: {joined}")


def build_openviking_config_from_env(env: dict[str, str] | None = None) -> dict[str, Any]:
    env = dict(os.environ if env is None else env)

    workspace = env.get("OPENVIKING_STORAGE_WORKSPACE", DEFAULT_STORAGE_WORKSPACE)
    vlm_api_key = _require_secret(env, "OPENVIKING_VLM_API_KEY", "OPENVIKING_ARK_API_KEY")
    embedding_api_key = _require_secret(
        env, "OPENVIKING_EMBEDDING_API_KEY", "OPENVIKING_ARK_API_KEY"
    )

    config: dict[str, Any] = {
        "storage": {
            "workspace": workspace,
        },
        "embedding": {
            "dense": {
                "provider": env.get("OPENVIKING_EMBEDDING_PROVIDER", "volcengine"),
                "model": env.get("OPENVIKING_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
                "api_key": embedding_api_key,
                "api_base": env.get(
                    "OPENVIKING_EMBEDDING_API_BASE", "https://ark.cn-beijing.volces.com/api/v3"
                ),
                "dimension": int(
                    env.get("OPENVIKING_EMBEDDING_DIMENSION", DEFAULT_EMBEDDING_DIMENSION)
                ),
                "input": env.get("OPENVIKING_EMBEDDING_INPUT", DEFAULT_EMBEDDING_INPUT),
            }
        },
        "vlm": {
            "provider": env.get("OPENVIKING_VLM_PROVIDER", "volcengine"),
            "model": env.get("OPENVIKING_VLM_MODEL", DEFAULT_VLM_MODEL),
            "api_key": vlm_api_key,
            "api_base": env.get("OPENVIKING_VLM_API_BASE", "https://ark.cn-beijing.volces.com/api/v3"),
            "temperature": float(env.get("OPENVIKING_VLM_TEMPERATURE", "0")),
        },
        "server": {
            "host": env.get("OPENVIKING_SERVER_HOST", DEFAULT_SERVER_HOST),
            "port": int(env.get("OPENVIKING_SERVER_PORT", DEFAULT_SERVER_PORT)),
        },
    }
    return config


def default_openviking_config_path() -> Path:
    return DEFAULT_CONFIG_PATH


def resolve_openviking_config_path(
    config_path: str | Path | None = None,
    env: dict[str, str] | None = None,
) -> Path | None:
    env = dict(os.environ if env is None else env)
    candidates: list[Path] = []
    if config_path:
        candidates.append(Path(config_path).expanduser().resolve())
    configured = env.get("OPENVIKING_CONFIG_FILE")
    if configured:
        candidates.append(Path(configured).expanduser().resolve())
    candidates.append(DEFAULT_CONFIG_PATH)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def openviking_server_url(config: dict[str, Any]) -> str:
    server = config.get("server") or {}
    host = str(server.get("host") or DEFAULT_SERVER_HOST).strip()
    port = int(server.get("port") or DEFAULT_SERVER_PORT)
    return f"http://{host}:{port}"


def read_openviking_overview(
    uri: str,
    *,
    config_path: str | Path | None = None,
    timeout: float = DEFAULT_OVERVIEW_TIMEOUT,
) -> str:
    resolved = resolve_openviking_config_path(config_path)
    if resolved is None:
        return _fallback_openviking_overview(uri)
    try:
        from openviking_cli.client.sync_http import SyncHTTPClient
    except Exception:
        return _fallback_openviking_overview(uri)
    try:
        config = read_openviking_config(resolved)
        client = SyncHTTPClient(url=openviking_server_url(config), timeout=timeout)
        client.initialize()
        try:
            return str(client.overview(uri) or "").strip()
        finally:
            client.close()
    except Exception:
        return _fallback_openviking_overview(uri)


def import_project_tree_to_openviking(
    project_id: str,
    *,
    config_path: str | Path | None = None,
    timeout: float = 120.0,
) -> bool:
    project_root = DEFAULT_IMPORT_TREE_ROOT / project_id
    if not project_root.exists():
        return False
    resolved = resolve_openviking_config_path(config_path)
    if resolved is None:
        return False
    config = read_openviking_config(resolved)
    try:
        import openviking  # noqa: F401
        from openviking_cli.client.sync_http import SyncHTTPClient
    except Exception:
        return False

    previous_no_proxy = os.environ.get("NO_PROXY")
    previous_no_proxy_lower = os.environ.get("no_proxy")
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    client = SyncHTTPClient(url=openviking_server_url(config), timeout=timeout)
    try:
        client.initialize()
        try:
            client.mkdir("viking://resources/projects")
        except Exception:
            pass
        try:
            client.rm(f"viking://resources/projects/{project_id}", recursive=True)
        except Exception:
            pass
        client.add_resource(
            str(project_root),
            parent="viking://resources/projects",
            wait=True,
        )
        return True
    except Exception:
        return False
    finally:
        try:
            client.close()
        except Exception:
            pass
        if previous_no_proxy is None:
            os.environ.pop("NO_PROXY", None)
        else:
            os.environ["NO_PROXY"] = previous_no_proxy
        if previous_no_proxy_lower is None:
            os.environ.pop("no_proxy", None)
        else:
            os.environ["no_proxy"] = previous_no_proxy_lower


def sync_project_tree_to_openviking(
    project_id: str,
    *,
    config_path: str | Path | None = None,
    timeout: float = 120.0,
) -> bool:
    project_root = DEFAULT_IMPORT_TREE_ROOT / project_id
    if not project_root.exists():
        return False
    resolved = resolve_openviking_config_path(config_path)
    if resolved is None:
        return False
    config = read_openviking_config(resolved)
    try:
        import openviking  # noqa: F401
        from openviking_cli.client.sync_http import SyncHTTPClient
    except Exception:
        return False

    previous_no_proxy = os.environ.get("NO_PROXY")
    previous_no_proxy_lower = os.environ.get("no_proxy")
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"
    client = SyncHTTPClient(url=openviking_server_url(config), timeout=timeout)
    try:
        client.initialize()
        for uri in (
            "viking://resources/projects",
            f"viking://resources/projects/{project_id}",
            f"viking://resources/projects/{project_id}/memory",
            f"viking://resources/projects/{project_id}/skills",
            f"viking://resources/projects/{project_id}/runtime",
            f"viking://resources/projects/{project_id}/dialogs",
        ):
            try:
                client.mkdir(uri)
            except Exception:
                pass
        for file_path in sorted(path for path in project_root.rglob("*") if path.is_file()):
            relative = file_path.relative_to(project_root)
            parent_uri = "viking://resources/projects/" + "/".join(
                [project_id] + list(relative.parts[:-1])
            )
            target_uri = f"{parent_uri}/{relative.name}"
            try:
                client.rm(target_uri, recursive=False)
            except Exception:
                pass
            client.add_resource(str(file_path), parent=parent_uri, wait=True)
        return True
    except Exception:
        return False
    finally:
        try:
            client.close()
        except Exception:
            pass
        if previous_no_proxy is None:
            os.environ.pop("NO_PROXY", None)
        else:
            os.environ["NO_PROXY"] = previous_no_proxy
        if previous_no_proxy_lower is None:
            os.environ.pop("no_proxy", None)
        else:
            os.environ["no_proxy"] = previous_no_proxy_lower


def _fallback_openviking_overview(uri: str) -> str:
    prefix = "viking://resources/projects/"
    if not uri.startswith(prefix):
        return ""
    remainder = uri.removeprefix(prefix).strip("/")
    if not remainder:
        return ""
    project_id = remainder.split("/", 1)[0]
    project_root = DEFAULT_IMPORT_TREE_ROOT / project_id
    if not project_root.exists():
        return ""
    snippets: list[str] = []
    for candidate in (
        project_root / "README.md",
        project_root / "runtime" / "project_brain.md",
        project_root / "runtime" / "memory_bundle.md",
    ):
        if candidate.exists():
            text = candidate.read_text(encoding="utf-8").strip()
            if text:
                snippets.append(" ".join(text.split()))
    if not snippets:
        return ""
    return " ".join(snippets)[:1200]


def validate_openviking_config(config: dict[str, Any]) -> None:
    from openviking_cli.utils.config.open_viking_config import OpenVikingConfig

    OpenVikingConfig.from_dict(config)


def write_openviking_config(config: dict[str, Any], output_path: str | Path) -> Path:
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def read_openviking_config(input_path: str | Path) -> dict[str, Any]:
    path = Path(input_path).expanduser().resolve()
    return json.loads(path.read_text(encoding="utf-8"))
