from dataclasses import dataclass


@dataclass
class AgentConfig:
    model: str
    openrouter_api_key: str
    telegram_bot_token: str
    telegram_allowed_chat_ids: list[str]
    telegram_polling_timeout_sec: int
    audit_log_path: str
    mcp_server_url: str
    mcp_api_key: str
    mcp_timeout_sec: int
    mcp_max_tool_rounds: int
    session_context_path: str
