import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from .microsoft_runtime_settings import read_settings
from .types import AgentConfig

# Zawsze repo root (…/jarvis1net/.env), niezależnie od cwd (np. systemd WorkingDirectory=src).
_DOTENV_PATH = Path(__file__).resolve().parents[2] / ".env"

# Short delegated names only; MSAL injects openid/profile/offline_access. Do not pass those three here.
_DEFAULT_MS_SCOPES = (
    "User.Read Mail.ReadWrite Mail.Send Calendars.ReadWrite Files.ReadWrite.All"
)

_DEFAULT_TELEGRAM_STARTUP_MESSAGE = (
    "Hej — jarvis1net wystartował po restarcie. "
    "Pamięć rozmowy w tym czacie została wyzerowana; możemy gadać od zera."
)


def _validated_display_timezone(raw: str) -> str:
    """IANA (np. Europe/Warsaw). Puste = brak — model nie dostaje reguły konwersji z UTC."""
    name = raw.strip()
    if not name:
        return ""
    try:
        ZoneInfo(name)
    except Exception:
        print(f"jarvis1net: DISPLAY_TIMEZONE={name!r} jest niepoprawne — ignoruję. Użyj np. Europe/Warsaw.")
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

    # Tenant z pliku runtime (Telegram /microsoft-set-client) ma pierwszeństwo przed .env —
    # inaczej MICROSOFT_TENANT_ID=organizations na VPS blokuje np. /microsoft-set-client … consumers (konto osobiste).
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

    return AgentConfig(
        model=os.getenv("MODEL", "o4-mini"),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_allowed_chat_ids=telegram_allowed_ids,
        telegram_notify_on_start=telegram_notify_on_start,
        telegram_clear_session_on_start=telegram_clear_session_on_start,
        telegram_startup_message=telegram_startup_message,
        telegram_polling_timeout_sec=telegram_polling_timeout,
        audit_log_path=audit_log_path,
        mcp_server_url=os.getenv("MCP_SERVER_URL", "https://mcp.jarvis1.net").strip().rstrip("/"),
        mcp_api_key=os.getenv("MCP_API_KEY", "").strip(),
        mcp_timeout_sec=mcp_timeout,
        mcp_max_tool_rounds=mcp_max_tool_rounds,
        mcp_tool_result_max_chars=mcp_tool_result_max_chars,
        mcp_microsoft_tool_result_max_chars=mcp_microsoft_tool_result_max_chars,
        mcp_chat_completion_max_tokens=mcp_chat_completion_max_tokens,
        session_context_path=session_context_path,
        microsoft_graph_access_token=graph_token,
        microsoft_client_id=ms_client,
        microsoft_tenant_id=ms_tenant,
        microsoft_graph_scopes=ms_scopes,
        microsoft_token_cache_path=ms_cache,
        display_timezone=display_timezone,
    )
