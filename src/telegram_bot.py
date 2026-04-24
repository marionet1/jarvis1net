import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

import requests

from core.agent import run_agent_turn
from core.audit import write_audit_event
from core.config import load_config
from core.llm import get_llm_reply
from core.microsoft_auth import (
    clear_token_cache_file,
    recommended_native_redirect_uris,
    run_device_code_login,
)
from core.microsoft_runtime_settings import (
    clear_settings_file,
    read_settings,
    save_merged_settings,
    settings_path,
    validate_client_id,
)
from core.session_context import get_session_store

# Natural phrases that clear chat memory (without slash commands).
_CLEAR_HISTORY_PHRASES = frozenset(
    {
        "clear history",
        "clear chat history",
        "reset chat",
        "start over",
        "clear conversation",
    }
)


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


def send_message(token: str, chat_id: int, text: str) -> None:
    for part in _chunk_text(text):
        telegram_request(
            token=token,
            method="sendMessage",
            payload={"chat_id": chat_id, "text": part},
        )


def process_message(
    chat_id: int,
    line: str,
    *,
    mcp_progress: Callable[[str], None] | None = None,
) -> list[str]:
    config = load_config()
    stripped = line.strip()
    lower = stripped.lower()
    command_base = lower.split()[0].split("@", 1)[0] if lower else ""

    if command_base in {"/start", "/help"}:
        return [
            "jarvis1net — chat naturally and ask for file operations, directory listings, MCP health checks, and more.\n"
            "The bot keeps short chat memory for this conversation. To clear it, send: 'clear history'.\n"
            "When MCP tools are used, you will first receive a short 'Using mcp-jarvis1net' message with tool name and arguments.\n"
            "Microsoft (Graph): wpisz w czacie /microsoft-set-client <Client-ID z Azure> [tenant], potem /microsoft-login (link + kod). "
            "Alternatywa: MICROSOFT_CLIENT_ID w .env. Szczegóły: /microsoft-show-settings."
        ]

    if command_base == "/microsoft-set-client":
        parts = stripped.split()
        if len(parts) < 2:
            return [
                "Użycie: /microsoft-set-client <Application-Client-ID> [tenant]\n"
                "tenant: np. common, organizations lub GUID katalogu (domyślnie common).\n"
                "W Azure: rejestracja aplikacji → public client + device code + Allow public client flows."
            ]
        cid = parts[1].strip()
        tenant = parts[2].strip() if len(parts) > 2 else "common"
        if not validate_client_id(cid):
            return ["Client ID musi być pełnym UUID (format 8-4-4-4-12 z Azure Portal)."]
        save_merged_settings(config.audit_log_path, {"client_id": cid, "tenant_id": tenant})
        return [
            f"Zapisano Client ID (tenant: {tenant}). Następnie wyślij /microsoft-login — bez restartu bota."
        ]

    if command_base in {"/microsoft-set-scopes", "/microsoft-scopes"}:
        parts = stripped.split(None, 1)
        if len(parts) < 2 or not parts[1].strip():
            scopes_txt = " ".join(config.microsoft_graph_scopes)
            return [
                "Użycie: /microsoft-set-scopes User.Read Mail.Read …\n"
                f"Aktualnie (efektywnie): {scopes_txt}"
            ]
        raw_scopes = parts[1].strip()
        scope_list = [s.strip() for s in raw_scopes.replace(",", " ").split() if s.strip()]
        if not scope_list:
            return ["Podaj co najmniej jeden scope."]
        save_merged_settings(config.audit_log_path, {"graph_scopes": scope_list})
        return [
            f"Zapisano {len(scope_list)} scope(y). Zgodne delegated permissions muszą być w Azure. Potem /microsoft-login."
        ]

    if command_base in {"/microsoft-show-settings", "/microsoft-config"}:
        rt = read_settings(config.audit_log_path)
        cid_env = os.getenv("MICROSOFT_CLIENT_ID", "").strip()
        src = "MICROSOFT_CLIENT_ID w .env" if cid_env else ("microsoft_agent_settings.json (czat/CLI)" if rt.get("client_id") else "brak")
        has_cache = Path(config.microsoft_token_cache_path).expanduser().exists()
        cid_show = config.microsoft_client_id or "(brak)"
        redirs = recommended_native_redirect_uris(config.microsoft_tenant_id)
        redir_lines = "\n".join(f"  • {u}" for u in redirs)
        lines = [
            "Microsoft — konfiguracja agenta:",
            f"- Client ID: {cid_show}",
            f"- Źródło Client ID: {src}",
            f"- Tenant: {config.microsoft_tenant_id}",
            f"- Scope: {' '.join(config.microsoft_graph_scopes)}",
            "- W Azure (Mobile/desktop) zarejestruj dokładnie TEN redirect (jeden wpis, zgodny z tenantem):",
            redir_lines,
            f"- Plik ustawień: {settings_path(config.audit_log_path)}",
            f"- Cache tokenów MSAL: {'tak' if has_cache else 'nie'}",
            "Komendy: /microsoft-set-client …, /microsoft-set-scopes …, /microsoft-login, /microsoft-logout, /microsoft-clear-runtime",
        ]
        return ["\n".join(lines)]

    if command_base in {"/microsoft-clear-runtime", "/microsoft-clear-settings"}:
        return [clear_settings_file(config.audit_log_path)]

    if command_base in {"/microsoft-login", "/msft-login"}:
        cfg = load_config()
        if not cfg.microsoft_client_id.strip():
            return [
                "Brak Client ID. W czacie wyślij: /microsoft-set-client <UUID z Azure> [tenant]\n"
                "albo ustaw MICROSOFT_CLIENT_ID w .env i zrestartuj bota."
            ]

        bot_token = cfg.telegram_bot_token

        def _worker() -> None:
            try:

                def _notify(msg: str) -> None:
                    send_message(bot_token, chat_id, msg)

                final = run_device_code_login(cfg, notify=_notify)
                send_message(bot_token, chat_id, final)
            except Exception as exc:
                send_message(bot_token, chat_id, f"Logowanie Microsoft nie powiodło się: {exc}")

        threading.Thread(target=_worker, daemon=True).start()
        return [
            "Microsoft: za chwilę dostaniesz wiadomość z linkiem i kodem — otwórz stronę, wpisz kod, zatwierdź uprawnienia. "
            "Może to potrwać kilka minut (czas na logowanie w przeglądarce)."
        ]

    if command_base in {"/microsoft-logout", "/msft-logout"}:
        cfg = load_config()
        return [clear_token_cache_file(cfg)]

    if lower in _CLEAR_HISTORY_PHRASES:
        store = get_session_store(config.session_context_path)
        store.clear_key(str(chat_id))
        store.save()
        return ["OK — chat history for this conversation has been cleared."]

    response = run_agent_turn(line, config)
    llm_text = get_llm_reply(
        user_input=line,
        model=response.selected_model,
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
            "model": response.selected_model,
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
                    send_message(token, chat_id, reply)
        except KeyboardInterrupt:
            break
        except Exception as exc:
            print(f"Telegram loop error: {exc}")
            time.sleep(2)


if __name__ == "__main__":
    run_bot()
