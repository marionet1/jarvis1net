from __future__ import annotations

import json
from typing import Any

import requests

from .types import AgentConfig


def _auth_headers(config: AgentConfig) -> dict[str, str]:
    if not config.mcp_api_key:
        raise RuntimeError("Missing MCP_API_KEY in .env")
    return {"Authorization": f"Bearer {config.mcp_api_key}"}


def _get(config: AgentConfig, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = requests.get(
        f"{config.mcp_server_url}{path}",
        params=params or {},
        headers=_auth_headers(config),
        timeout=config.mcp_timeout_sec,
    )
    response.raise_for_status()
    return response.json()


def _post_json(config: AgentConfig, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(
        f"{config.mcp_server_url}{path}",
        json=payload,
        headers={**_auth_headers(config), "Content-Type": "application/json"},
        timeout=config.mcp_timeout_sec,
    )
    response.raise_for_status()
    return response.json()


def mcp_health(config: AgentConfig) -> dict[str, Any]:
    response = requests.get(
        f"{config.mcp_server_url}/health",
        headers=_auth_headers(config),
        timeout=config.mcp_timeout_sec,
    )
    response.raise_for_status()
    return response.json()


def mcp_fs_list(config: AgentConfig, path: str) -> dict[str, Any]:
    return _get(config, "/v1/tools/filesystem/list", {"path": path})


def mcp_fs_stat(config: AgentConfig, path: str) -> dict[str, Any]:
    return _get(config, "/v1/tools/filesystem/stat", {"path": path})


def mcp_fs_read(config: AgentConfig, path: str, max_bytes: int | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"path": path}
    if max_bytes is not None:
        params["max_bytes"] = max_bytes
    return _get(config, "/v1/tools/filesystem/read", params)


def mcp_fs_write(
    config: AgentConfig,
    path: str,
    content: str,
    encoding: str = "utf-8",
    create_parents: bool = False,
) -> dict[str, Any]:
    return _post_json(
        config,
        "/v1/tools/filesystem/write",
        {
            "path": path,
            "content": content,
            "encoding": encoding,
            "create_parents": create_parents,
        },
    )


def mcp_fs_mkdir(config: AgentConfig, path: str, parents: bool = False) -> dict[str, Any]:
    return _post_json(config, "/v1/tools/filesystem/mkdir", {"path": path, "parents": parents})


def mcp_fs_delete(config: AgentConfig, path: str) -> dict[str, Any]:
    return _post_json(config, "/v1/tools/filesystem/delete", {"path": path})


def mcp_fs_rename(config: AgentConfig, from_path: str, to_path: str) -> dict[str, Any]:
    return _post_json(
        config,
        "/v1/tools/filesystem/rename",
        {"from_path": from_path, "to_path": to_path},
    )


def mcp_list_files(config: AgentConfig, path: str) -> dict[str, Any]:
    """Alias zgodny ze starszym kodem (Telegram / CLI)."""
    return mcp_fs_list(config, path)
