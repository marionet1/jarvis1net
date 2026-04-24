"""Dynamic MCP tool loading and execution helpers."""

from __future__ import annotations

import json
from typing import Any

import requests

from .types import AgentConfig


def _auth_headers(config: AgentConfig) -> dict[str, str]:
    if not config.mcp_api_key:
        raise RuntimeError("Missing MCP_API_KEY in .env")
    return {"Authorization": f"Bearer {config.mcp_api_key}"}


def mcp_get(config: AgentConfig, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = requests.get(
        f"{config.mcp_server_url}{path}",
        params=params or {},
        headers=_auth_headers(config),
        timeout=config.mcp_timeout_sec,
    )
    response.raise_for_status()
    return response.json()


def mcp_post_json(
    config: AgentConfig,
    path: str,
    payload: dict[str, Any],
    *,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    headers = {**_auth_headers(config), "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    response = requests.post(
        f"{config.mcp_server_url}{path}",
        json=payload,
        headers=headers,
        timeout=config.mcp_timeout_sec,
    )
    response.raise_for_status()
    return response.json()


def mcp_health(config: AgentConfig) -> dict[str, Any]:
    return mcp_get(config, "/health")


def _http_error_payload(exc: requests.HTTPError) -> dict[str, Any]:
    body = ""
    if exc.response is not None:
        try:
            body = exc.response.text[:4000]
        except OSError:
            body = ""
    return {
        "ok": False,
        "http_status": exc.response.status_code if exc.response is not None else None,
        "error": str(exc),
        "body": body,
    }


def load_mcp_tools(config: AgentConfig) -> list[dict[str, Any]]:
    """Loads OpenAI-style function schemas directly from MCP server manifest."""
    data = mcp_get(config, "/v1/tools")
    tools = data.get("tools")
    if not isinstance(tools, list):
        raise RuntimeError("Invalid MCP tools manifest response.")
    return tools


def run_mcp_tool(name: str, arguments: dict[str, Any], config: AgentConfig) -> str:
    """Runs one MCP tool call via generic /v1/tools/call and returns JSON for role=tool."""
    try:
        payload = {"name": name, "arguments": arguments or {}}
        extra: dict[str, str] | None = None
        if name.startswith("microsoft_") and config.microsoft_graph_access_token:
            raw = config.microsoft_graph_access_token.strip()
            bearer = raw if raw.lower().startswith("bearer ") else f"Bearer {raw}"
            extra = {"X-Graph-Authorization": bearer}
        out = mcp_post_json(config, "/v1/tools/call", payload, extra_headers=extra)
        if isinstance(out, dict) and isinstance(out.get("result"), dict):
            return json.dumps(out["result"], ensure_ascii=False)
        return json.dumps(out, ensure_ascii=False)
    except requests.HTTPError as exc:
        return json.dumps(_http_error_payload(exc), ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)

