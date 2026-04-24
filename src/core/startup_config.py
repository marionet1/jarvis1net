"""Startup configuration checks: required keys, MCP health, optional Graph."""

from __future__ import annotations

from dataclasses import dataclass, field

from .jarvis_runtime_settings import clear_jarvis_runtime_file
from .mcp_tools import mcp_health
from .microsoft_auth import clear_token_cache_file, resolve_graph_access_token
from .microsoft_runtime_settings import clear_settings_file
from .session_context import get_session_store
from .types import AgentConfig


@dataclass
class StartupCheckResult:
    """`blocking` empty = LLM chat is allowed. `mcp_summary` / `graph_summary` short lines for the OK report."""

    ok: bool
    mcp_summary: str = ""
    graph_summary: str = ""
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def run_startup_checks(config: AgentConfig) -> StartupCheckResult:
    blocking: list[str] = []
    warnings: list[str] = []

    if not config.mcp_api_key.strip():
        mcp_summary = "disabled (no MCP_API_KEY)"
    else:
        try:
            mcp_health(config)
            mcp_summary = f"{config.mcp_server_url} — OK"
        except Exception as exc:
            mcp_summary = f"{config.mcp_server_url} — error ({type(exc).__name__})"
            warnings.append(
                f"MCP: {str(exc)[:200]}. Set a valid key: /jarvis-set-mcp-key <key>"
            )

    tok = resolve_graph_access_token(config)
    if tok:
        graph_summary = "OK"
    elif not config.microsoft_client_id.strip():
        graph_summary = "none"
        warnings.append(
            "Graph: no token and no Client ID — /microsoft-set-client <UUID> then /microsoft-login "
            "or /microsoft-set-graph-token"
        )
    else:
        graph_summary = "waiting for login (/microsoft-login)"

    if not config.openrouter_api_key.strip():
        blocking.append(
            "OPENROUTER_API_KEY missing. In chat: /jarvis-set-openrouter-key <key> (saved next to logs; channel leak risk)."
        )

    if not config.telegram_allowed_chat_ids:
        warnings.append(
            "TELEGRAM_ALLOWED_CHAT_IDS empty — anyone with the bot link can chat; production: set chat_id list in .env."
        )

    return StartupCheckResult(
        ok=len(blocking) == 0,
        mcp_summary=mcp_summary,
        graph_summary=graph_summary,
        blocking=blocking,
        warnings=warnings,
    )


def format_startup_report_plain(result: StartupCheckResult, *, title: str = "jarvis1net configuration") -> str:
    """Short plain text for Telegram / logs."""
    if result.ok:
        lines = [
            title,
            "",
            "Configuration OK.",
            f"MCP: {result.mcp_summary}",
            f"Microsoft Graph: {result.graph_summary}",
        ]
        for w in result.warnings:
            lines.append(f"(warning) {w}")
        return "\n".join(lines)

    lines = [title, "", "Configuration incomplete — add the missing items:"]
    for b in result.blocking:
        lines.append(f"- {b}")
    for w in result.warnings:
        lines.append(f"- {w}")
    lines.append("")
    lines.append(
        "From chat (know the risk): /jarvis-set-openrouter-key …, /jarvis-set-mcp-key …; "
        "Microsoft: /microsoft-set-client + /microsoft-login or /microsoft-set-graph-token. "
        "Clear saved bot data: /jarvis-config-reset."
    )
    return "\n".join(lines)


def reset_runtime_agent_state(config: AgentConfig) -> list[str]:
    """
    Clears chat-saved runtime + MSAL cache + conversation memory + jarvis_runtime_secrets.json. Does not remove .env.
    """
    out: list[str] = []
    out.append(clear_settings_file(config.audit_log_path))
    out.append(clear_jarvis_runtime_file(config.audit_log_path))
    out.append(clear_token_cache_file(config))
    store = get_session_store(config.session_context_path)
    store.clear_all_sessions()
    store.save()
    out.append("Cleared conversation memory (all sessions in session_paths.json).")
    return out
