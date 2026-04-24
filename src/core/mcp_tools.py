"""MCP tools: stdio (Node mcp-jarvis1net)."""

from __future__ import annotations

import json
from typing import Any

from .microsoft_agent import resolve_graph_access_token
from .mcp_stdio_client import get_stdio_client
from .types import AgentConfig


def mcp_can_use_tools(config: AgentConfig) -> bool:
    return bool(config.mcp_stdio_command.strip()) and bool(config.mcp_stdio_args)


def load_mcp_tools(config: AgentConfig) -> list[dict[str, Any]]:
    """OpenAI-style function schemas from the MCP server over stdio."""
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


def run_mcp_tool(name: str, arguments: dict[str, Any], config: AgentConfig) -> str:
    """Run one stdio tool and return JSON for role=tool."""
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
