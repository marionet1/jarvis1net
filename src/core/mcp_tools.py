"""MCP tools: HTTP (legacy) or stdio (Node mcp-jarvis1net)."""

from __future__ import annotations

import json
from typing import Any

import requests

from .microsoft_agent import resolve_graph_access_token
from .mcp_stdio_client import get_stdio_client
from .types import AgentConfig


def mcp_can_use_tools(config: AgentConfig) -> bool:
    if config.mcp_mode == "http":
        return bool(config.mcp_api_key.strip())
    return bool(config.mcp_stdio_command.strip()) and bool(config.mcp_stdio_args)


def _auth_headers(config: AgentConfig) -> dict[str, str]:
    if not config.mcp_api_key:
        raise RuntimeError("Missing MCP_API_KEY in .env (HTTP mode).")
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
    if config.mcp_mode != "http":
        return {"ok": True, "mode": "stdio"}
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


def _load_mcp_tools_http(config: AgentConfig) -> list[dict[str, Any]]:
    data = mcp_get(config, "/v1/tools")
    tools = data.get("tools")
    if not isinstance(tools, list):
        raise RuntimeError("Invalid MCP tools manifest response.")
    return tools


def load_mcp_tools(config: AgentConfig) -> list[dict[str, Any]]:
    """OpenAI-style function schemas from the MCP server."""
    if config.mcp_mode == "http":
        if not config.mcp_api_key.strip():
            raise RuntimeError("MCP HTTP mode requires MCP_API_KEY.")
        return _load_mcp_tools_http(config)
    if not mcp_can_use_tools(config):
        raise RuntimeError("MCP stdio: set MCP_STDIO_ARGS (JSON) or MCP_STDIO_NODE_SCRIPT in .env.")
    client = get_stdio_client(
        config.mcp_stdio_command,
        list(config.mcp_stdio_args),
        None,
    )
    return client.list_tools(float(config.mcp_timeout_sec))


def filter_mcp_tools_when_graph_token_present(
    config: AgentConfig, tools: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """When the agent already sends a Graph token, `microsoft_integration_status` is redundant — drop it from the manifest."""
    if not resolve_graph_access_token(config):
        return tools
    out: list[dict[str, Any]] = []
    for item in tools:
        fn = item.get("function")
        if isinstance(fn, dict) and fn.get("name") == "microsoft_integration_status":
            continue
        out.append(item)
    return out


def _run_mcp_tool_http(
    name: str, arguments: dict[str, Any], config: AgentConfig
) -> str:
    try:
        payload = {"name": name, "arguments": arguments or {}}
        extra: dict[str, str] | None = None
        if name.startswith("microsoft_"):
            token = resolve_graph_access_token(config)
            if token:
                extra = {"X-Graph-Authorization": f"Bearer {token}"}
        out = mcp_post_json(config, "/v1/tools/call", payload, extra_headers=extra)
        if isinstance(out, dict) and isinstance(out.get("result"), dict):
            return json.dumps(out["result"], ensure_ascii=False)
        return json.dumps(out, ensure_ascii=False)
    except requests.HTTPError as exc:
        return json.dumps(_http_error_payload(exc), ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


def run_mcp_tool(name: str, arguments: dict[str, Any], config: AgentConfig) -> str:
    """Run one tool (HTTP or stdio) and return JSON for role=tool."""
    if config.mcp_mode == "http":
        return _run_mcp_tool_http(name, arguments, config)
    if not mcp_can_use_tools(config):
        return json.dumps({"ok": False, "error": "MCP stdio is not configured."}, ensure_ascii=False)
    args = dict(arguments or {})
    if name.startswith("microsoft_"):
        token = resolve_graph_access_token(config)
        if token:
            args["graph_access_token"] = token
    try:
        client = get_stdio_client(
            config.mcp_stdio_command,
            list(config.mcp_stdio_args),
            None,
        )
        return client.call_tool(name, args, float(config.mcp_timeout_sec))
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
