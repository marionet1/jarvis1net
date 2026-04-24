from dataclasses import dataclass


@dataclass
class AgentConfig:
    model: str
    openrouter_api_key: str
    telegram_bot_token: str
    telegram_allowed_chat_ids: list[str]
    telegram_notify_on_start: bool
    telegram_clear_session_on_start: bool
    telegram_startup_message: str
    telegram_polling_timeout_sec: int
    audit_log_path: str
    mcp_server_url: str
    mcp_api_key: str
    mcp_timeout_sec: int
    mcp_max_tool_rounds: int
    mcp_tool_result_max_chars: int
    mcp_microsoft_tool_result_max_chars: int
    mcp_chat_completion_max_tokens: int
    session_context_path: str
    microsoft_graph_access_token: str
    microsoft_client_id: str
    microsoft_tenant_id: str
    microsoft_graph_scopes: list[str]
    microsoft_token_cache_path: str
    display_timezone: str
