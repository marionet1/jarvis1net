import os
from pathlib import Path

from dotenv import load_dotenv

from .types import AgentConfig


def load_config() -> AgentConfig:
    load_dotenv()
    telegram_allowed_raw = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
    telegram_allowed_ids = [x.strip() for x in telegram_allowed_raw.split(",") if x.strip()]
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
    mcp_tool_rounds_raw = os.getenv("MCP_MAX_TOOL_ROUNDS", "10").strip()
    try:
        mcp_max_tool_rounds = max(1, min(32, int(mcp_tool_rounds_raw)))
    except ValueError:
        mcp_max_tool_rounds = 10

    audit_log_path = os.getenv("AUDIT_LOG_PATH", "/home/jump/jarvis1net/logs/audit.jsonl")
    session_ctx_env = os.getenv("SESSION_CONTEXT_PATH", "").strip()
    if session_ctx_env:
        session_context_path = session_ctx_env
    else:
        session_context_path = str(Path(audit_log_path).expanduser().resolve().parent / "session_paths.json")

    graph_token = os.getenv("MICROSOFT_GRAPH_ACCESS_TOKEN", "").strip()
    ms_client = os.getenv("MICROSOFT_CLIENT_ID", "").strip()
    ms_tenant = os.getenv("MICROSOFT_TENANT_ID", "common").strip() or "common"
    scopes_raw = os.getenv(
        "MICROSOFT_GRAPH_SCOPES",
        "offline_access User.Read Mail.Read Calendars.Read Files.Read.All",
    ).strip()
    ms_scopes = [s.strip() for s in scopes_raw.replace(",", " ").split() if s.strip()]
    ms_cache_env = os.getenv("MICROSOFT_TOKEN_CACHE_PATH", "").strip()
    if ms_cache_env:
        ms_cache = ms_cache_env
    else:
        ms_cache = str(Path(audit_log_path).expanduser().resolve().parent / "ms_graph_token_cache.json")

    return AgentConfig(
        model=os.getenv("MODEL", "o4-mini"),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_allowed_chat_ids=telegram_allowed_ids,
        telegram_polling_timeout_sec=telegram_polling_timeout,
        audit_log_path=audit_log_path,
        mcp_server_url=os.getenv("MCP_SERVER_URL", "https://mcp.jarvis1.net").strip().rstrip("/"),
        mcp_api_key=os.getenv("MCP_API_KEY", "").strip(),
        mcp_timeout_sec=mcp_timeout,
        mcp_max_tool_rounds=mcp_max_tool_rounds,
        session_context_path=session_context_path,
        microsoft_graph_access_token=graph_token,
        microsoft_client_id=ms_client,
        microsoft_tenant_id=ms_tenant,
        microsoft_graph_scopes=ms_scopes,
        microsoft_token_cache_path=ms_cache,
    )
