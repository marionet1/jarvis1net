import os
import json
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from integrations.mcp import load_mcp_tools
from integrations.microsoft import (
    clear_settings_file,
    clear_token_cache_file,
    read_settings,
    resolve_graph_access_token,
)

from .jarvis_runtime_settings import clear_jarvis_runtime_file, read_jarvis_runtime
from .session_context import get_session_store
from .types import AgentConfig

_DOTENV_PATH = Path(__file__).resolve().parents[2] / ".env"
_RUNTIME_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "runtime_config.json"

_DEFAULT_MS_SCOPES = "User.Read Mail.ReadWrite Mail.Send Calendars.ReadWrite Files.ReadWrite.All"
_DEFAULT_TELEGRAM_STARTUP_MESSAGE = (
    "Hi — jarvis1net restarted. "
    "Conversation memory in this chat was cleared; we can continue from a fresh context."
)


def _validated_display_timezone(raw: str) -> str:
    name = raw.strip()
    if not name:
        return ""
    try:
        ZoneInfo(name)
    except Exception:
        print(f"jarvis1net: DISPLAY_TIMEZONE={name!r} is invalid — ignoring. Use e.g. Europe/Warsaw.")
        return ""
    return name


def _as_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("1", "true", "yes", "on", "tak"):
            return True
        if v in ("0", "false", "no", "off", "nie"):
            return False
    return default


def _load_runtime_config() -> dict[str, object]:
    try:
        raw = json.loads(_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except Exception:
        pass
    return {}


def _parse_chat_ids_csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def load_config() -> AgentConfig:
    load_dotenv(_DOTENV_PATH)
    cfg = _load_runtime_config()

    allowed_env = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
    telegram_allowed_ids = _parse_chat_ids_csv(allowed_env)
    telegram_notify_on_start = _as_bool(cfg.get("telegram_notify_on_start", True), True)
    telegram_clear_session_on_start = _as_bool(cfg.get("telegram_clear_session_on_start", True), True)
    telegram_startup_raw = str(cfg.get("telegram_startup_message", "")).strip()
    telegram_startup_message = telegram_startup_raw or _DEFAULT_TELEGRAM_STARTUP_MESSAGE
    telegram_timeout_raw = str(cfg.get("telegram_polling_timeout_sec", 25)).strip()
    try:
        telegram_polling_timeout = max(5, int(telegram_timeout_raw))
    except ValueError:
        telegram_polling_timeout = 25
    mcp_timeout_raw = str(cfg.get("mcp_timeout_sec", 15)).strip()
    try:
        mcp_timeout = max(3, int(mcp_timeout_raw))
    except ValueError:
        mcp_timeout = 15
    mcp_tool_rounds_raw = str(cfg.get("mcp_max_tool_rounds", 18)).strip()
    try:
        mcp_max_tool_rounds = max(1, min(48, int(mcp_tool_rounds_raw)))
    except ValueError:
        mcp_max_tool_rounds = 18
    mcp_tool_cap_raw = str(cfg.get("mcp_tool_result_max_chars", 40000)).strip()
    try:
        mcp_tool_result_max_chars = max(4000, min(200_000, int(mcp_tool_cap_raw)))
    except ValueError:
        mcp_tool_result_max_chars = 40_000

    ms_tool_cap_raw = str(cfg.get("mcp_microsoft_tool_result_max_chars", 12000)).strip()
    try:
        mcp_microsoft_tool_result_max_chars = max(3000, min(mcp_tool_result_max_chars, int(ms_tool_cap_raw)))
    except ValueError:
        mcp_microsoft_tool_result_max_chars = min(12_000, mcp_tool_result_max_chars)

    chat_max_out_raw = str(cfg.get("mcp_chat_completion_max_tokens", 10240)).strip()
    try:
        mcp_chat_completion_max_tokens = max(2048, min(32_768, int(chat_max_out_raw)))
    except ValueError:
        mcp_chat_completion_max_tokens = 10_240

    audit_log_path = str(cfg.get("audit_log_path", "/app/data/audit.jsonl")).strip() or "/app/data/audit.jsonl"
    session_ctx_env = str(cfg.get("session_context_path", "")).strip()
    if session_ctx_env:
        session_context_path = session_ctx_env
    else:
        session_context_path = str(Path(audit_log_path).expanduser().resolve().parent / "session_paths.json")

    graph_token = os.getenv("MCP_GRAPH_ACCESS_TOKEN", "").strip()
    rt = read_settings(audit_log_path)
    if not graph_token:
        rt_tok = rt.get("graph_access_token")
        if isinstance(rt_tok, str) and rt_tok.strip():
            graph_token = rt_tok.strip()

    ms_client = str(cfg.get("microsoft_client_id", "")).strip() or str(rt.get("client_id") or "").strip()
    ms_tenant_env = str(cfg.get("microsoft_tenant_id", "")).strip()
    rt_tenant = str(rt.get("tenant_id") or "").strip()
    if rt_tenant:
        ms_tenant = rt_tenant
    elif ms_tenant_env:
        ms_tenant = ms_tenant_env
    else:
        ms_tenant = "consumers"

    scopes_raw = cfg.get("microsoft_graph_scopes", [])
    if isinstance(scopes_raw, list) and scopes_raw:
        ms_scopes = [str(s).strip() for s in scopes_raw if str(s).strip()]
    else:
        gs = rt.get("graph_scopes")
        if isinstance(gs, list) and gs:
            ms_scopes = [str(x).strip() for x in gs if str(x).strip()]
        elif isinstance(gs, str) and gs.strip():
            ms_scopes = [s.strip() for s in gs.replace(",", " ").split() if s.strip()]
        else:
            ms_scopes = [s.strip() for s in _DEFAULT_MS_SCOPES.split() if s.strip()]
    ms_cache_env = str(cfg.get("microsoft_token_cache_path", "")).strip()
    if ms_cache_env:
        ms_cache = ms_cache_env
    else:
        ms_cache = str(Path(audit_log_path).expanduser().resolve().parent / "ms_graph_token_cache.json")

    display_timezone = _validated_display_timezone(str(cfg.get("display_timezone", "")).strip())
    openrouter_show_cost_estimate = _as_bool(cfg.get("openrouter_show_cost_estimate", True), True)
    mcp_stdio_cmd = str(cfg.get("mcp_stdio_command", "python3")).strip() or "python3"
    raw_args = cfg.get("mcp_stdio_args", [])
    mcp_stdio_args = [str(x) for x in raw_args if str(x).strip()] if isinstance(raw_args, list) else []

    openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    jrt = read_jarvis_runtime(audit_log_path)
    j_or = jrt.get("openrouter_api_key")
    if isinstance(j_or, str) and j_or.strip():
        openrouter_key = j_or.strip()

    return AgentConfig(
        model=str(cfg.get("model", "o4-mini")).strip() or "o4-mini",
        openrouter_api_key=openrouter_key,
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_allowed_chat_ids=telegram_allowed_ids,
        telegram_notify_on_start=telegram_notify_on_start,
        telegram_clear_session_on_start=telegram_clear_session_on_start,
        telegram_startup_message=telegram_startup_message,
        telegram_polling_timeout_sec=telegram_polling_timeout,
        audit_log_path=audit_log_path,
        mcp_stdio_command=mcp_stdio_cmd,
        mcp_stdio_args=mcp_stdio_args,
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
        jarvis_no_docker_exit_restart=_as_bool(cfg.get("jarvis_no_docker_exit_restart", False), False),
    )


@dataclass
class StartupCheckResult:
    ok: bool
    mcp_summary: str = ""
    graph_summary: str = ""
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def run_startup_checks(config: AgentConfig) -> StartupCheckResult:
    blocking: list[str] = []
    warnings: list[str] = []

    if not (config.mcp_stdio_args and config.mcp_stdio_command):
        mcp_summary = "stdio not configured (set mcp_stdio_args)"
        warnings.append("MCP: configure mcp_stdio_command + mcp_stdio_args in config/runtime_config.json.")
    else:
        try:
            _ = load_mcp_tools(config)
            mcp_summary = f"stdio {config.mcp_stdio_command} {' '.join(config.mcp_stdio_args)} — OK"
        except Exception as exc:
            mcp_summary = f"stdio — error ({type(exc).__name__})"
            warnings.append(f"MCP stdio: {str(exc)[:200]}")

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
            "OpenRouter API key missing (each deployment uses its own). "
            "In Telegram: /jarvis-set-openrouter-key <key> — key is saved in jarvis_runtime_secrets.json "
            "next to other data (Docker: under /app/data, persists after restart). "
            "Or set OPENROUTER_API_KEY in .env on the server. Chat channel = leak risk if others read the chat."
        )

    if not config.telegram_allowed_chat_ids:
        warnings.append(
            "telegram allowed_chat_ids empty — anyone with the bot link can chat; production: set "
            "TELEGRAM_ALLOWED_CHAT_IDS in env."
        )

    return StartupCheckResult(
        ok=len(blocking) == 0,
        mcp_summary=mcp_summary,
        graph_summary=graph_summary,
        blocking=blocking,
        warnings=warnings,
    )


def format_startup_report_plain(result: StartupCheckResult, *, title: str = "jarvis1net configuration") -> str:
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
        "First: /jarvis-set-openrouter-key <key from https://openrouter.ai/keys> (saved on disk, survives restarts). "
        "Then MCP: stdio via config/runtime_config.json (Docker/README); "
        "Microsoft: /microsoft-set-client + /microsoft-login or /microsoft-set-graph-token. "
        "Clear saved keys: /jarvis-config-reset."
    )
    return "\n".join(lines)


def reset_runtime_agent_state(config: AgentConfig) -> list[str]:
    out: list[str] = []
    out.append(clear_settings_file(config.audit_log_path))
    out.append(clear_jarvis_runtime_file(config.audit_log_path))
    out.append(clear_token_cache_file(config))
    store = get_session_store(config.session_context_path)
    store.clear_all_sessions()
    store.save()
    out.append("Cleared conversation memory (all sessions in session_paths.json).")
    return out
