from __future__ import annotations

import html
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import requests

from core.audit import write_audit_event
from core.chat_phrases import CLEAR_HISTORY_PHRASES
from core.config import (
    StartupCheckResult,
    format_startup_report_plain,
    load_config,
    reset_runtime_agent_state,
    run_startup_checks,
)
from core.jarvis_runtime_settings import save_merged_jarvis_runtime
from core.llm import get_llm_reply
from core.mcp_tools import filter_mcp_tools_when_graph_token_present, load_mcp_tools
from core.microsoft_agent import (
    clear_settings_file,
    clear_token_cache_file,
    read_settings,
    recommended_native_redirect_uris,
    run_device_code_login,
    save_merged_settings,
    settings_path,
    validate_client_id,
)
from core.session_context import get_session_store
from core.types import AgentConfig


def _looks_like_telegram_chat_key(key: str) -> bool:
    """Session key is usually numeric chat_id (groups may use a negative id)."""
    s = key.strip()
    if not s:
        return False
    if s.startswith("-"):
        s = s[1:]
    return s.isdigit()


def run_telegram_startup_hooks(
    config: AgentConfig, *, startup_check: StartupCheckResult | None = None
) -> None:
    """After process restart: optionally clear sessions, send startup message and config report."""
    chk = startup_check if startup_check is not None else run_startup_checks(config)
    if not config.telegram_clear_session_on_start and not config.telegram_notify_on_start:
        return
    store = get_session_store(config.session_context_path)

    notify_targets: list[str] = []
    if config.telegram_notify_on_start:
        if config.telegram_allowed_chat_ids:
            notify_targets = list(config.telegram_allowed_chat_ids)
        else:
            notify_targets = [k for k in store.list_session_keys() if _looks_like_telegram_chat_key(k)]

    if config.telegram_clear_session_on_start:
        if config.telegram_allowed_chat_ids:
            for cid in config.telegram_allowed_chat_ids:
                store.clear_key(cid)
        else:
            store.clear_all_sessions()
        store.save()

    if not config.telegram_notify_on_start:
        return
    if not notify_targets:
        print(
            "jarvis1net: TELEGRAM_NOTIFY_ON_START is on but there are no recipients — set TELEGRAM_ALLOWED_CHAT_IDS "
            "or message the bot once (session is saved), then the next restart will send startup text to a known chat_id."
        )
        return
    for cid_s in notify_targets:
        try:
            send_message(config.telegram_bot_token, int(cid_s), config.telegram_startup_message)
        except Exception as exc:
            print(f"jarvis1net: startup message to chat_id={cid_s} failed: {exc}")

    report = format_startup_report_plain(chk, title="jarvis1net configuration (after restart)")
    for cid_s in notify_targets:
        try:
            send_message(config.telegram_bot_token, int(cid_s), report)
        except Exception as exc:
            print(f"jarvis1net: config report to chat_id={cid_s} failed: {exc}")


# Exact message text (no slash) — avoids accidental “restart” inside a sentence.
_RESTART_BOT_PHRASES = frozenset(
    {
        "restart bot",
        "restart the bot",
        "restart bota",
        "restartuj bota",
        "zrestartuj bota",
        "restart jarvis",
        "restart jarvis1net",
    }
)


def _restart_from_chat_allowed(config: AgentConfig, chat_id_s: str) -> bool:
    return bool(config.telegram_allowed_chat_ids) and chat_id_s in config.telegram_allowed_chat_ids


def _jarvis_secrets_from_chat_allowed(config: AgentConfig, chat_id_s: str) -> bool:
    """If TELEGRAM_ALLOWED_CHAT_IDS is empty anyone can save keys (bootstrap risk); else whitelist only."""
    if not config.telegram_allowed_chat_ids:
        return True
    return chat_id_s in config.telegram_allowed_chat_ids


def _schedule_telegram_self_restart() -> None:
    """Delayed user systemd restart so Telegram can receive the reply first."""

    def worker() -> None:
        time.sleep(1.5)
        try:
            subprocess.run(
                ["systemctl", "--user", "restart", "jarvis1net-telegram.service"],
                check=False,
                timeout=120,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            print(f"jarvis1net: self-restart (systemctl) failed: {exc}")

    threading.Thread(target=worker, daemon=True).start()


@dataclass(frozen=True)
class TelegramOut:
    """Telegram payload: plain text or HTML (parse_mode)."""

    text: str
    parse_mode: str | None = None


_INFO_HTML_MAX = 3800


def _cmd_line(cmd: str, description: str) -> str:
    """One BotFather-style line: /command - description (HTML)."""
    return f"{html.escape(cmd)} - {html.escape(description)}\n"


def _commands_info_botfather_style_html() -> str:
    """Bold sections + “/command - description” list like @BotFather."""
    parts: list[str] = [
        "You can control the bot with these commands:\n\n",
        "<b>General</b>\n\n",
        _cmd_line("/start", "Help and quick setup hints"),
        _cmd_line("/help", "Same as /start"),
        _cmd_line("/info", "This list: commands + MCP tools"),
        _cmd_line("/jarvis-info", "Alias for /info"),
        "\n",
        "<b>Bot and MCP limits</b>\n\n",
        _cmd_line("/restart", "Restart process (TELEGRAM_ALLOWED_CHAT_IDS only)"),
        _cmd_line("/jarvis-restart", "Alias for /restart"),
        _cmd_line("/jarvis-limits", "Tool rounds, max JSON chars, timeout"),
        _cmd_line("/mcp-limits", "Alias for /jarvis-limits"),
        _cmd_line("/limits", "Alias for /jarvis-limits"),
        _cmd_line("/jarvis-config-check", "Checks .env + MCP + Graph (same as on startup)"),
        _cmd_line("/config-check", "Alias for /jarvis-config-check"),
        _cmd_line("/jarvis-config-reset", "Clears MS runtime + chat-saved keys + MSAL cache + chat memory"),
        _cmd_line("/config-reset", "Alias for /jarvis-config-reset"),
        _cmd_line("/jarvis-set-openrouter-key", "Saves OPENROUTER key from chat next to logs"),
        _cmd_line("/jarvis-set-mcp-key", "Saves MCP_API_KEY from chat next to logs"),
        "\n",
        "<b>Conversation memory</b>\n\n",
        _cmd_line("clear history", "Clears context (also: clear chat history, reset chat, start over, clear conversation)"),
        "\n",
        "<b>Microsoft Graph</b>\n\n",
        _cmd_line("/microsoft-set-client", "Azure Client ID + optional tenant → runtime file"),
        _cmd_line("/microsoft-set-tenant", "Tenant: consumers, organizations, common, or GUID"),
        _cmd_line("/microsoft-set-scopes", "Scope list (e.g. Mail.ReadWrite); alias: /microsoft-scopes"),
        _cmd_line("/microsoft-show-settings", "MS summary + redirect URIs; alias: /microsoft-config"),
        _cmd_line("/microsoft-login", "Device code login in background; alias: /msft-login"),
        _cmd_line("/microsoft-logout", "Logout + MSAL cache; alias: /msft-logout"),
        _cmd_line("/microsoft-set-graph-token", "Paste Graph access token; alias: /microsoft-paste-token"),
        _cmd_line("/microsoft-clear-runtime", "Clears runtime settings file; alias: /microsoft-clear-settings"),
        "\n",
        "<b>No slash (BotFather-style phrases)</b>\n\n",
        _cmd_line(
            "restart bot",
            "Same as /restart (exact phrase; also: restart the bot, restart bota, restart jarvis)",
        ),
    ]
    return "".join(parts)


def build_info_html_chunks(config: AgentConfig) -> list[str]:
    """One or more HTML chunks (&lt; 4096 chars each) — commands + MCP tools."""
    cmd_html = _commands_info_botfather_style_html()
    head = (
        "<b>jarvis1net — /info</b>\n\n"
        + cmd_html
        + "\n<b>MCP tools</b>\n\n"
        + f"<i>Server:</i> <code>{html.escape(config.mcp_server_url.strip())}</code>\n\n"
    )
    chunks: list[str] = []
    current = head
    mcp_note = ""

    if not config.mcp_api_key.strip():
        mcp_note = "<b>MCP</b>: <i>no MCP_API_KEY — tool list unavailable.</i>\n"
        one = (current + mcp_note)[:_INFO_HTML_MAX]
        return [one]

    try:
        tools = filter_mcp_tools_when_graph_token_present(config, load_mcp_tools(config))
    except Exception as exc:
        err = html.escape(str(exc)[:500])
        one = (current + f"<b>MCP</b>: <i>failed to load tool manifest:</i>\n<pre>{err}</pre>")[:_INFO_HTML_MAX]
        return [one]

    sorted_specs = sorted(
        (t for t in tools if isinstance(t, dict)),
        key=lambda x: str((x.get("function") or {}).get("name") or ""),
    )
    tool_blocks: list[str] = []
    for spec in sorted_specs:
        fn = spec.get("function")
        if not isinstance(fn, dict):
            continue
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        desc = str(fn.get("description") or "").strip().replace("\r\n", "\n").replace("\n", " ")
        if len(desc) > 380:
            desc = desc[:377] + "…"
        if desc:
            tool_blocks.append(f"<code>{html.escape(name)}</code> - {html.escape(desc)}\n")
        else:
            tool_blocks.append(f"<code>{html.escape(name)}</code>\n")

    if not tool_blocks:
        current += "<i>(empty tool manifest)</i>"
        return [current[:_INFO_HTML_MAX]]

    for block in tool_blocks:
        if len(current) + len(block) > _INFO_HTML_MAX:
            chunks.append(current)
            current = "<b>MCP tools</b> <i>(continued)</i>\n\n" + block
        else:
            current += block
    if current.strip():
        chunks.append(current)
    return chunks if chunks else [head[:_INFO_HTML_MAX]]


def _chunk_text(text: str, limit: int = 3900) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = text
    while len(current) > limit:
        split_at = current.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(current[:split_at])
        current = current[split_at:].lstrip("\n")
    if current:
        chunks.append(current)
    return chunks


def telegram_request(token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    response = requests.post(url, json=payload, timeout=40)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return data


def send_message(token: str, chat_id: int, text: str, *, parse_mode: str | None = None) -> None:
    for part in _chunk_text(text):
        payload: dict[str, Any] = {"chat_id": chat_id, "text": part}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        telegram_request(
            token=token,
            method="sendMessage",
            payload=payload,
        )


def process_message(
    chat_id: int,
    line: str,
    *,
    mcp_progress: Callable[[str], None] | None = None,
) -> list[str | TelegramOut]:
    config = load_config()
    stripped = line.strip()
    lower = stripped.lower()
    command_base = lower.split()[0].split("@", 1)[0] if lower else ""
    chat_id_s = str(chat_id)

    if command_base in {"/restart", "/jarvis-restart"} or lower in _RESTART_BOT_PHRASES:
        if not _restart_from_chat_allowed(config, chat_id_s):
            if not config.telegram_allowed_chat_ids:
                return [
                    "Restart from chat works only if you set TELEGRAM_ALLOWED_CHAT_IDS in .env "
                    "(then only those chats may send /restart)."
                ]
            return ["No permission to restart from this chat."]
        _schedule_telegram_self_restart()
        return [
            "OK — in about 2 s I will restart the bot process (jarvis1net-telegram). "
            "You should get a startup message shortly (if TELEGRAM_NOTIFY_ON_START=1)."
        ]

    if command_base in {"/start", "/help"}:
        return [
            "jarvis1net — chat naturally and ask for file operations, directory listings, MCP health checks, and more.\n"
            "The bot keeps short chat memory for this conversation. To clear it, send: 'clear history'.\n"
            "After a bot process restart, chat memory may be cleared automatically and you may get a short notice "
            "(default on — see TELEGRAM_NOTIFY_ON_START / TELEGRAM_CLEAR_SESSION_ON_START in .env).\n"
            "When MCP tools are used, you will first receive a short 'Using mcp-jarvis1net' message with tool name and arguments.\n"
            "Microsoft (Graph): /microsoft-set-client <Client-ID> [tenant], then /microsoft-login. "
            "Personal Microsoft account (@outlook etc.): tenant **consumers** + in Azure redirect …/consumers/oauth2/nativeclient. "
            "Quick tenant change: /microsoft-set-tenant consumers | organizations | common. "
            "Token from PC: /microsoft-set-graph-token. Details: /microsoft-show-settings.\n"
            "MCP limits (tool rounds / JSON truncation): /jarvis-limits\n"
            "Keys from chat (leak risk): **/jarvis-set-openrouter-key** …, **/jarvis-set-mcp-key** …. "
            "Check: **/jarvis-config-check**. Reset saved bot data: **/jarvis-config-reset** (same guard as /restart).\n"
            "Restart the bot (TELEGRAM_ALLOWED_CHAT_IDS only): **/restart** or e.g. “restart bot”.\n"
            "Full command list + MCP tools (HTML): **/info**"
        ]

    if command_base in {"/info", "/jarvis-info"}:
        return [TelegramOut(chunk, "HTML") for chunk in build_info_html_chunks(config)]

    if command_base in {"/jarvis-config-check", "/config-check"}:
        chk = run_startup_checks(config)
        return [format_startup_report_plain(chk, title="jarvis1net configuration (on demand)")]

    if command_base == "/jarvis-set-openrouter-key":
        if not _jarvis_secrets_from_chat_allowed(config, chat_id_s):
            return ["No permission (this chat is not in TELEGRAM_ALLOWED_CHAT_IDS)."]
        parts = stripped.split(None, 1)
        key = parts[1].strip() if len(parts) > 1 else ""
        if not key:
            return ["Usage: /jarvis-set-openrouter-key <key from https://openrouter.ai/keys>"]
        if len(key) < 12:
            return ["Key looks too short."]
        save_merged_jarvis_runtime(config.audit_log_path, {"openrouter_api_key": key})
        return [
            "Saved OpenRouter key to jarvis_runtime_secrets.json (next to logs). "
            "It applies from the next message (no restart). /jarvis-config-check — preview."
        ]

    if command_base == "/jarvis-set-mcp-key":
        if not _jarvis_secrets_from_chat_allowed(config, chat_id_s):
            return ["No permission (this chat is not in TELEGRAM_ALLOWED_CHAT_IDS)."]
        parts = stripped.split(None, 1)
        key = parts[1].strip() if len(parts) > 1 else ""
        if not key:
            return ["Usage: /jarvis-set-mcp-key <MCP API key>"]
        if len(key) < 8:
            return ["MCP key looks too short."]
        save_merged_jarvis_runtime(config.audit_log_path, {"mcp_api_key": key})
        return [
            "Saved MCP_API_KEY to jarvis_runtime_secrets.json. "
            "It applies from the next message (no restart). /jarvis-config-check — preview."
        ]

    if command_base in {"/jarvis-config-reset", "/config-reset"}:
        if not _restart_from_chat_allowed(config, chat_id_s):
            if not config.telegram_allowed_chat_ids:
                return [
                    "Reset from chat works only if you set TELEGRAM_ALLOWED_CHAT_IDS in .env "
                    "(only those chats may send /jarvis-config-reset)."
                ]
            return ["No permission to reset from this chat."]
        lines = reset_runtime_agent_state(config)
        return [
            "Reset saved bot data (does not change .env on disk):\n"
            + "\n".join(f"- {x}" for x in lines)
            + "\n\nNext: /jarvis-set-openrouter-key …, /jarvis-set-mcp-key …, Microsoft: /microsoft-set-client + "
            "/microsoft-login or /microsoft-set-graph-token. Check: /jarvis-config-check."
        ]

    if command_base in {"/jarvis-limits", "/mcp-limits", "/limits"}:
        return [
            "jarvis1net — MCP limits for this instance:\n"
            f"- MCP_MAX_TOOL_ROUNDS (effective): {config.mcp_max_tool_rounds}\n"
            f"- MCP_TOOL_RESULT_MAX_CHARS: {config.mcp_tool_result_max_chars}\n"
            f"- MCP_MICROSOFT_TOOL_RESULT_MAX_CHARS (microsoft_*): {config.mcp_microsoft_tool_result_max_chars}\n"
            f"- MCP_CHAT_COMPLETION_MAX_TOKENS: {config.mcp_chat_completion_max_tokens}\n"
            f"- MCP_TIMEOUT_SEC: {config.mcp_timeout_sec}\n"
            f"- OPENROUTER_SHOW_COST_ESTIMATE: {1 if config.openrouter_show_cost_estimate else 0} "
            "(footer ~USD from /api/v1/models pricing)\n"
            f"- DISPLAY_TIMEZONE: {config.display_timezone or '(none — model quotes Graph times as UTC/Z)'}\n"
            f"- .env file: {Path(__file__).resolve().parents[1] / '.env'}\n"
            "To change: edit .env in the repo root (not src/) and restart the bot."
        ]

    if command_base == "/microsoft-set-client":
        parts = stripped.split()
        if len(parts) < 2:
            return [
                "Usage: /microsoft-set-client <Application-Client-ID> [tenant]\n"
                "tenant: e.g. organizations, common, consumers, or directory GUID (default organizations — work accounts).\n"
                "In Azure: app registration → public client + device code + Allow public client flows."
            ]
        cid = parts[1].strip()
        tenant = parts[2].strip() if len(parts) > 2 else "organizations"
        if not validate_client_id(cid):
            return ["Client ID must be a full UUID (8-4-4-4-12 from Azure Portal)."]
        save_merged_settings(config.audit_log_path, {"client_id": cid, "tenant_id": tenant})
        return [
            f"Saved Client ID (tenant: {tenant}). Next send /microsoft-login — no bot restart needed."
        ]

    if command_base == "/microsoft-set-tenant":
        parts = stripped.split()
        if len(parts) < 2:
            return [
                "Usage: /microsoft-set-tenant <consumers|organizations|common|directory-GUID>\n"
                "— consumers: personal Microsoft account (Azure redirect: …/consumers/oauth2/nativeclient).\n"
                "— organizations: work/school account (redirect …/organizations/…).\n"
                "— common: MSA + orgs (redirect …/common/…; can be finicky).\n"
                "Then /microsoft-logout and /microsoft-login."
            ]
        raw = parts[1].strip()
        t = raw.casefold()
        ok = t in ("common", "organizations", "consumers") or validate_client_id(raw)
        if not ok:
            return ["Unknown tenant — use consumers, organizations, common, or directory GUID from Azure."]
        save_merged_settings(config.audit_log_path, {"tenant_id": raw})
        return [
            f"Saved tenant: {raw}. Next /microsoft-logout → /microsoft-login (Azure redirect must match this segment)."
        ]

    if command_base in {"/microsoft-set-scopes", "/microsoft-scopes"}:
        parts = stripped.split(None, 1)
        if len(parts) < 2 or not parts[1].strip():
            scopes_txt = " ".join(config.microsoft_graph_scopes)
            return [
                "Usage: /microsoft-set-scopes User.Read Mail.Read …\n"
                f"Currently (effective): {scopes_txt}"
            ]
        raw_scopes = parts[1].strip()
        scope_list = [s.strip() for s in raw_scopes.replace(",", " ").split() if s.strip()]
        if not scope_list:
            return ["Provide at least one scope."]
        save_merged_settings(config.audit_log_path, {"graph_scopes": scope_list})
        return [
            f"Saved {len(scope_list)} scope(s). Matching delegated permissions must exist in Azure. Then /microsoft-login."
        ]

    if command_base in {"/microsoft-show-settings", "/microsoft-config"}:
        rt = read_settings(config.audit_log_path)
        cid_env = os.getenv("MICROSOFT_CLIENT_ID", "").strip()
        src = "MICROSOFT_CLIENT_ID in .env" if cid_env else (
            "microsoft_agent_settings.json (chat/CLI)" if rt.get("client_id") else "none"
        )
        has_cache = Path(config.microsoft_token_cache_path).expanduser().exists()
        cid_show = config.microsoft_client_id or "(none)"
        ten_env = os.getenv("MICROSOFT_TENANT_ID", "").strip()
        ten_rt = str(rt.get("tenant_id") or "").strip()
        if ten_rt:
            ten_src = "microsoft_agent_settings.json (overrides .env)"
        elif ten_env:
            ten_src = "MICROSOFT_TENANT_ID in .env"
        else:
            ten_src = "default organizations (no file and no env)"
        tok_env = bool(os.getenv("MICROSOFT_GRAPH_ACCESS_TOKEN", "").strip())
        tok_rt = bool(
            isinstance(rt.get("graph_access_token"), str) and str(rt.get("graph_access_token")).strip()
        )
        if tok_env:
            tok_src = "MICROSOFT_GRAPH_ACCESS_TOKEN (.env)"
        elif tok_rt:
            tok_src = "graph_access_token (microsoft_agent_settings.json, e.g. /microsoft-set-graph-token)"
        else:
            tok_src = "none (MSAL cache after /microsoft-login)"
        redirs = recommended_native_redirect_uris(config.microsoft_tenant_id)
        redir_lines = "\n".join(f"  • {u}" for u in redirs)
        lines = [
            "Microsoft — agent configuration:",
            f"- Client ID: {cid_show}",
            f"- Client ID source: {src}",
            f"- Tenant: {config.microsoft_tenant_id} (source: {ten_src})",
            f"- Scopes: {' '.join(config.microsoft_graph_scopes)}",
            f"- Graph token (header to MCP): {tok_src}",
            "- In Azure (Mobile/desktop) register exactly THIS redirect (one entry, must match tenant):",
            redir_lines,
            f"- Settings file: {settings_path(config.audit_log_path)}",
            f"- MSAL token cache: {'yes' if has_cache else 'no'}",
            "Commands: /microsoft-set-client …, /microsoft-set-tenant …, /microsoft-set-scopes …, /microsoft-login, "
            "/microsoft-set-graph-token …, /microsoft-logout, /microsoft-clear-runtime",
        ]
        return ["\n".join(lines)]

    if command_base in {"/microsoft-clear-runtime", "/microsoft-clear-settings"}:
        return [clear_settings_file(config.audit_log_path)]

    if command_base in {"/microsoft-set-graph-token", "/microsoft-paste-token"}:
        parts = stripped.split(None, 1)
        if len(parts) < 2 or not parts[1].strip():
            return [
                "Usage: /microsoft-set-graph-token <access_token>\n\n"
                "On your PC (after az login): "
                "az account get-access-token --resource https://graph.microsoft.com -o tsv\n"
                "Paste the output here (one line). Token is saved to microsoft_agent_settings.json next to logs — "
                "do not share the chat. Overrides .env only if MICROSOFT_GRAPH_ACCESS_TOKEN is empty."
            ]
        tok = parts[1].strip()
        if tok.casefold().startswith("bearer "):
            tok = tok[7:].strip()
        if len(tok) < 30:
            return ["Token looks too short — ensure you pasted the full access_token (JWT)."]
        save_merged_settings(config.audit_log_path, {"graph_access_token": tok})
        return [
            "Saved Graph token (runtime). Next microsoft_* MCP calls use it instead of MSAL. "
            "Logout: /microsoft-logout (clears this token too)."
        ]

    if command_base in {"/microsoft-login", "/msft-login"}:
        cfg = load_config()
        if not cfg.microsoft_client_id.strip():
            return [
                "No Client ID. In chat send: /microsoft-set-client <UUID from Azure> [tenant]\n"
                "or set MICROSOFT_CLIENT_ID in .env and restart the bot."
            ]

        bot_token = cfg.telegram_bot_token

        def _worker() -> None:
            try:

                def _notify(msg: str) -> None:
                    send_message(bot_token, chat_id, msg)

                final = run_device_code_login(cfg, notify=_notify)
                send_message(bot_token, chat_id, final)
            except Exception as exc:
                send_message(bot_token, chat_id, f"Microsoft login failed: {exc}")

        threading.Thread(target=_worker, daemon=True).start()
        return [
            "Microsoft: you will get a message with a link and code shortly.\n"
            "— Open only the URL from that message (e.g. https://microsoft.com/devicelogin), enter the code, finish sign-in.\n"
            "— Do not paste or manually open …/oauth2/nativeclient — that is the OAuth redirect URI, not the login page; "
            "opening it without the full login chain yields a response_type error.\n"
            "— If stuck: try Edge or Chrome (Brave may strip URL params) or allow microsoft.com and login.microsoftonline.com.\n"
            "In Azure: Allow public client flows and one Mobile/desktop redirect matching the tenant (/microsoft-show-settings).\n"
            "This may take a few minutes."
        ]

    if command_base in {"/microsoft-logout", "/msft-logout"}:
        cfg = load_config()
        save_merged_settings(cfg.audit_log_path, {"graph_access_token": None})
        return [clear_token_cache_file(cfg)]

    if lower in CLEAR_HISTORY_PHRASES:
        store = get_session_store(config.session_context_path)
        store.clear_key(str(chat_id))
        store.save()
        return ["OK — chat history for this conversation has been cleared."]

    llm_text = get_llm_reply(
        user_input=line,
        model=config.model,
        config=config,
        session_key=str(chat_id),
        before_tool_round=mcp_progress,
    )
    write_audit_event(
        log_path=config.audit_log_path,
        event_type="chat_response",
        payload={
            "source": "telegram",
            "chat_id": chat_id,
            "model": config.model,
            "trigger": line,
        },
    )
    return [llm_text]


def run_bot() -> None:
    config = load_config()
    token = config.telegram_bot_token
    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN in .env")

    offset = 0
    print("Telegram bot started (chat-only long polling).")
    startup_chk = run_startup_checks(config)
    print(format_startup_report_plain(startup_chk, title="jarvis1net — configuration report (stdout)"))
    run_telegram_startup_hooks(config, startup_check=startup_chk)

    while True:
        try:
            data = telegram_request(
                token=token,
                method="getUpdates",
                payload={
                    "offset": offset,
                    "timeout": config.telegram_polling_timeout_sec,
                    "allowed_updates": ["message"],
                },
            )
            updates = data.get("result", [])
            for item in updates:
                offset = max(offset, int(item["update_id"]) + 1)
                message = item.get("message", {})
                text = message.get("text")
                if not text:
                    continue
                chat = message.get("chat", {})
                chat_id = int(chat.get("id"))
                chat_id_s = str(chat_id)

                if config.telegram_allowed_chat_ids and chat_id_s not in config.telegram_allowed_chat_ids:
                    send_message(token, chat_id, "Access denied for this bot.")
                    continue

                replies = process_message(
                    chat_id=chat_id,
                    line=text,
                    mcp_progress=lambda msg: send_message(token, chat_id, msg),
                )
                for reply in replies:
                    if isinstance(reply, TelegramOut):
                        send_message(token, chat_id, reply.text, parse_mode=reply.parse_mode)
                    else:
                        send_message(token, chat_id, reply)
        except KeyboardInterrupt:
            break
        except Exception as exc:
            print(f"Telegram loop error: {exc}")
            time.sleep(2)


if __name__ == "__main__":
    run_bot()
