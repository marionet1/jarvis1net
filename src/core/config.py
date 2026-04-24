import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from .jarvis_runtime_settings import clear_jarvis_runtime_file, read_jarvis_runtime
from .mcp_tools import mcp_health
from .microsoft_agent import (
    clear_settings_file,
    clear_token_cache_file,
    read_settings,
    resolve_graph_access_token,
)
from .session_context import get_session_store
from .types import AgentConfig

# Always repo root (…/jarvis1net/.env), regardless of cwd (e.g. systemd WorkingDirectory=src).
_DOTENV_PATH = Path(__file__).resolve().parents[2] / ".env"

# Short delegated names only; MSAL injects openid/profile/offline_access. Do not pass those three here.
_DEFAULT_MS_SCOPES = (
    "User.Read Mail.ReadWrite Mail.Send Calendars.ReadWrite Files.ReadWrite.All"
)

_DEFAULT_TELEGRAM_STARTUP_MESSAGE = (
    "Hi — jarvis1net restarted. "
    "Conversation memory in this chat was cleared; we can continue from a fresh context."
)


def _validated_display_timezone(raw: str) -> str:
    """IANA name (e.g. Europe/Warsaw). Empty = none — model does not get UTC→local conversion rules."""
    name = raw.strip()
    if not name:
        return ""
    try:
        ZoneInfo(name)
    except Exception:
        print(f"jarvis1net: DISPLAY_TIMEZONE={name!r} is invalid — ignoring. Use e.g. Europe/Warsaw.")
        return ""
    return name


def _env_bool(key: str, default: bool) -> bool:
    v = os.getenv(key, "").strip().lower()
    if v == "":
        return default
    return v in ("1", "true", "yes", "on", "tak")


def load_config() -> AgentConfig:
    load_dotenv(_DOTENV_PATH)
    telegram_allowed_raw = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
    telegram_allowed_ids = [x.strip() for x in telegram_allowed_raw.split(",") if x.strip()]
    telegram_notify_on_start = _env_bool("TELEGRAM_NOTIFY_ON_START", True)
    telegram_clear_session_on_start = _env_bool("TELEGRAM_CLEAR_SESSION_ON_START", True)
    telegram_startup_msg_raw = os.getenv("TELEGRAM_STARTUP_MESSAGE", "").strip()
    telegram_startup_message = (
        telegram_startup_msg_raw if telegram_startup_msg_raw else _DEFAULT_TELEGRAM_STARTUP_MESSAGE
    )
    telegram_timeout_raw = os.getenv("TELEGRAM_POLLING_TIMEOUT_SEC", "25").strip()
    try:
        telegram_polling_timeout = max(5, int(telegram_timeout_raw))
    except ValueError:
        telegram_polling_timeout = 25
    mcp_timeout_raw = os.getenv("MCP_TIMEOUT_SEC", "15").strip()
    try:
        mcp_timeout = max(3, int(mcp_timeout_raw))
    except ValueError:
        mcp_timeout = 15
    mcp_tool_rounds_raw = os.getenv("MCP_MAX_TOOL_ROUNDS", "18").strip()
    try:
        mcp_max_tool_rounds = max(1, min(48, int(mcp_tool_rounds_raw)))
    except ValueError:
        mcp_max_tool_rounds = 18
    mcp_tool_cap_raw = os.getenv("MCP_TOOL_RESULT_MAX_CHARS", "40000").strip()
    try:
        mcp_tool_result_max_chars = max(4000, min(200_000, int(mcp_tool_cap_raw)))
    except ValueError:
        mcp_tool_result_max_chars = 40_000

    ms_tool_cap_raw = os.getenv("MCP_MICROSOFT_TOOL_RESULT_MAX_CHARS", "12000").strip()
    try:
        mcp_microsoft_tool_result_max_chars = max(
            3000, min(mcp_tool_result_max_chars, int(ms_tool_cap_raw))
        )
    except ValueError:
        mcp_microsoft_tool_result_max_chars = min(12_000, mcp_tool_result_max_chars)

    chat_max_out_raw = os.getenv("MCP_CHAT_COMPLETION_MAX_TOKENS", "10240").strip()
    try:
        mcp_chat_completion_max_tokens = max(2048, min(32_768, int(chat_max_out_raw)))
    except ValueError:
        mcp_chat_completion_max_tokens = 10_240

    audit_log_path = os.getenv("AUDIT_LOG_PATH", "/home/jump/jarvis1net/logs/audit.jsonl")
    session_ctx_env = os.getenv("SESSION_CONTEXT_PATH", "").strip()
    if session_ctx_env:
        session_context_path = session_ctx_env
    else:
        session_context_path = str(Path(audit_log_path).expanduser().resolve().parent / "session_paths.json")

    graph_token = os.getenv("MICROSOFT_GRAPH_ACCESS_TOKEN", "").strip()
    rt = read_settings(audit_log_path)
    if not graph_token:
        rt_tok = rt.get("graph_access_token")
        if isinstance(rt_tok, str) and rt_tok.strip():
            graph_token = rt_tok.strip()

    ms_client = os.getenv("MICROSOFT_CLIENT_ID", "").strip() or str(rt.get("client_id") or "").strip()

    # Runtime file tenant (Telegram /microsoft-set-client) wins over .env —
    # otherwise MICROSOFT_TENANT_ID=organizations on the host would block e.g. … consumers (personal account).
    ms_tenant_env = os.getenv("MICROSOFT_TENANT_ID", "").strip()
    rt_tenant = str(rt.get("tenant_id") or "").strip()
    if rt_tenant:
        ms_tenant = rt_tenant
    elif ms_tenant_env:
        ms_tenant = ms_tenant_env
    else:
        ms_tenant = "organizations"

    scopes_env = os.getenv("MICROSOFT_GRAPH_SCOPES", "").strip()
    if scopes_env:
        ms_scopes = [s.strip() for s in scopes_env.replace(",", " ").split() if s.strip()]
    else:
        gs = rt.get("graph_scopes")
        if isinstance(gs, list) and gs:
            ms_scopes = [str(x).strip() for x in gs if str(x).strip()]
        elif isinstance(gs, str) and gs.strip():
            ms_scopes = [s.strip() for s in gs.replace(",", " ").split() if s.strip()]
        else:
            ms_scopes = [s.strip() for s in _DEFAULT_MS_SCOPES.split() if s.strip()]
    ms_cache_env = os.getenv("MICROSOFT_TOKEN_CACHE_PATH", "").strip()
    if ms_cache_env:
        ms_cache = ms_cache_env
    else:
        ms_cache = str(Path(audit_log_path).expanduser().resolve().parent / "ms_graph_token_cache.json")

    display_timezone = _validated_display_timezone(os.getenv("DISPLAY_TIMEZONE", "").strip())
    openrouter_show_cost_estimate = _env_bool("OPENROUTER_SHOW_COST_ESTIMATE", True)

    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    mcp_key = os.getenv("MCP_API_KEY", "").strip()
    jrt = read_jarvis_runtime(audit_log_path)
    j_or = jrt.get("openrouter_api_key")
    if isinstance(j_or, str) and j_or.strip():
        openrouter_key = j_or.strip()
    j_mcp = jrt.get("mcp_api_key")
    if isinstance(j_mcp, str) and j_mcp.strip():
        mcp_key = j_mcp.strip()

    return AgentConfig(
        model=os.getenv("MODEL", "o4-mini"),
        openrouter_api_key=openrouter_key,
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_allowed_chat_ids=telegram_allowed_ids,
        telegram_notify_on_start=telegram_notify_on_start,
        telegram_clear_session_on_start=telegram_clear_session_on_start,
        telegram_startup_message=telegram_startup_message,
        telegram_polling_timeout_sec=telegram_polling_timeout,
        audit_log_path=audit_log_path,
        mcp_server_url=os.getenv("MCP_SERVER_URL", "https://mcp.jarvis1.net").strip().rstrip("/"),
        mcp_api_key=mcp_key,
        mcp_timeout_sec=mcp_timeout,
        mcp_max_tool_rounds=mcp_max_tool_rounds,
        mcp_tool_result_max_chars=mcp_tool_result_max_chars,
        mcp_microsoft_tool_result_max_chars=mcp_microsoft_tool_result_max_chars,
        mcp_chat_completion_max_tokens=mcp_chat_completion_max_tokens,
        openrouter_show_cost_estimate=openrouter_show_cost_estimate,
        session_context_path=session_context_path,
        microsoft_graph_access_token=graph_token,
        microsoft_client_id=ms_client,
        microsoft_tenant_id=ms_tenant,
        microsoft_graph_scopes=ms_scopes,
        microsoft_token_cache_path=ms_cache,
        display_timezone=display_timezone,
    )


# --- Startup checks and reset (Telegram / CLI reports) -----------------------------------------


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
