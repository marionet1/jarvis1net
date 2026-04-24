import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

import requests

from core.agent import run_agent_turn
from core.audit import write_audit_event
from core.config import load_config
from core.types import AgentConfig
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


def _looks_like_telegram_chat_key(key: str) -> bool:
    """Klucz sesji = zwykle numeryczny chat_id (grupy mogą mieć ujemny ID)."""
    s = key.strip()
    if not s:
        return False
    if s.startswith("-"):
        s = s[1:]
    return s.isdigit()


def run_telegram_startup_hooks(config: AgentConfig) -> None:
    """Po restarcie procesu: opcjonalnie czyści session_paths i wysyła powitanie na czaty docelowe."""
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
            "jarvis1net: TELEGRAM_NOTIFY_ON_START włączone, ale brak odbiorców — ustaw TELEGRAM_ALLOWED_CHAT_IDS "
            "albo napisz do bota raz (zapisze sesję), potem kolejny restart wyśle powitanie na znany chat_id."
        )
        return
    for cid_s in notify_targets:
        try:
            send_message(config.telegram_bot_token, int(cid_s), config.telegram_startup_message)
        except Exception as exc:
            print(f"jarvis1net: powitanie startowe do chat_id={cid_s} nie powiodło się: {exc}")


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
            "Po restarcie procesu bota pamięć czatu może być automatycznie wyzerowana i dostaniesz krótką wiadomość "
            "(domyślnie włączone — patrz TELEGRAM_NOTIFY_ON_START / TELEGRAM_CLEAR_SESSION_ON_START w .env).\n"
            "When MCP tools are used, you will first receive a short 'Using mcp-jarvis1net' message with tool name and arguments.\n"
            "Microsoft (Graph): /microsoft-set-client <Client-ID> [tenant], potem /microsoft-login. "
            "Konto osobiste (@outlook itd.): tenant **consumers** + w Azure redirect …/consumers/oauth2/nativeclient. "
            "Szybka zmiana tenantu: /microsoft-set-tenant consumers | organizations | common. "
            "Token z PC: /microsoft-set-graph-token. Szczegóły: /microsoft-show-settings.\n"
            "Limit MCP (rundy narzędzi / obcięcie JSON): /jarvis-limits"
        ]

    if command_base in {"/jarvis-limits", "/mcp-limits", "/limits"}:
        return [
            "jarvis1net — limity MCP w tej instancji:\n"
            f"- MCP_MAX_TOOL_ROUNDS (efektywnie): {config.mcp_max_tool_rounds}\n"
            f"- MCP_TOOL_RESULT_MAX_CHARS: {config.mcp_tool_result_max_chars}\n"
            f"- MCP_MICROSOFT_TOOL_RESULT_MAX_CHARS (wyniki microsoft_*): {config.mcp_microsoft_tool_result_max_chars}\n"
            f"- MCP_CHAT_COMPLETION_MAX_TOKENS: {config.mcp_chat_completion_max_tokens}\n"
            f"- MCP_TIMEOUT_SEC: {config.mcp_timeout_sec}\n"
            f"- Plik .env: {Path(__file__).resolve().parents[1] / '.env'}\n"
            "Zmiana: edytuj .env w katalogu repo (nie src/) i zrestartuj bota."
        ]

    if command_base == "/microsoft-set-client":
        parts = stripped.split()
        if len(parts) < 2:
            return [
                "Użycie: /microsoft-set-client <Application-Client-ID> [tenant]\n"
                "tenant: np. organizations, common, consumers lub GUID katalogu (domyślnie organizations — konta służbowe).\n"
                "W Azure: rejestracja aplikacji → public client + device code + Allow public client flows."
            ]
        cid = parts[1].strip()
        tenant = parts[2].strip() if len(parts) > 2 else "organizations"
        if not validate_client_id(cid):
            return ["Client ID musi być pełnym UUID (format 8-4-4-4-12 z Azure Portal)."]
        save_merged_settings(config.audit_log_path, {"client_id": cid, "tenant_id": tenant})
        return [
            f"Zapisano Client ID (tenant: {tenant}). Następnie wyślij /microsoft-login — bez restartu bota."
        ]

    if command_base == "/microsoft-set-tenant":
        parts = stripped.split()
        if len(parts) < 2:
            return [
                "Użycie: /microsoft-set-tenant <consumers|organizations|common|GUID-katalogu>\n"
                "— consumers: konto osobiste Microsoft (redirect w Azure: …/consumers/oauth2/nativeclient).\n"
                "— organizations: konto służbowe (redirect …/organizations/…).\n"
                "— common: MSA + organizacje (redirect …/common/…; bywa kapryśne).\n"
                "Potem /microsoft-logout i /microsoft-login."
            ]
        raw = parts[1].strip()
        t = raw.casefold()
        ok = t in ("common", "organizations", "consumers") or validate_client_id(raw)
        if not ok:
            return ["Nieznany tenant — użyj consumers, organizations, common albo GUID katalogu z Azure."]
        save_merged_settings(config.audit_log_path, {"tenant_id": raw})
        return [f"Zapisano tenant: {raw}. Następnie /microsoft-logout → /microsoft-login (redirect w Azure musi pasować do tego segmentu)."]

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
        ten_env = os.getenv("MICROSOFT_TENANT_ID", "").strip()
        ten_rt = str(rt.get("tenant_id") or "").strip()
        if ten_rt:
            ten_src = "microsoft_agent_settings.json (nadpisuje .env)"
        elif ten_env:
            ten_src = "MICROSOFT_TENANT_ID w .env"
        else:
            ten_src = "domyślnie organizations (brak pliku i env)"
        tok_env = bool(os.getenv("MICROSOFT_GRAPH_ACCESS_TOKEN", "").strip())
        tok_rt = bool(
            isinstance(rt.get("graph_access_token"), str) and str(rt.get("graph_access_token")).strip()
        )
        if tok_env:
            tok_src = "MICROSOFT_GRAPH_ACCESS_TOKEN (.env)"
        elif tok_rt:
            tok_src = "graph_access_token (microsoft_agent_settings.json, np. /microsoft-set-graph-token)"
        else:
            tok_src = "brak (MSAL cache po /microsoft-login)"
        redirs = recommended_native_redirect_uris(config.microsoft_tenant_id)
        redir_lines = "\n".join(f"  • {u}" for u in redirs)
        lines = [
            "Microsoft — konfiguracja agenta:",
            f"- Client ID: {cid_show}",
            f"- Źródło Client ID: {src}",
            f"- Tenant: {config.microsoft_tenant_id} (źródło: {ten_src})",
            f"- Scope: {' '.join(config.microsoft_graph_scopes)}",
            f"- Token Graph (nagłówek do MCP): {tok_src}",
            "- W Azure (Mobile/desktop) zarejestruj dokładnie TEN redirect (jeden wpis, zgodny z tenantem):",
            redir_lines,
            f"- Plik ustawień: {settings_path(config.audit_log_path)}",
            f"- Cache tokenów MSAL: {'tak' if has_cache else 'nie'}",
            "Komendy: /microsoft-set-client …, /microsoft-set-tenant …, /microsoft-set-scopes …, /microsoft-login, "
            "/microsoft-set-graph-token …, /microsoft-logout, /microsoft-clear-runtime",
        ]
        return ["\n".join(lines)]

    if command_base in {"/microsoft-clear-runtime", "/microsoft-clear-settings"}:
        return [clear_settings_file(config.audit_log_path)]

    if command_base in {"/microsoft-set-graph-token", "/microsoft-paste-token"}:
        parts = stripped.split(None, 1)
        if len(parts) < 2 or not parts[1].strip():
            return [
                "Użycie: /microsoft-set-graph-token <access_token>\n\n"
                "Na swoim PC (po az login): "
                "az account get-access-token --resource https://graph.microsoft.com -o tsv\n"
                "Wklej wynik tutaj (jedna linia). Token zapisuje się w microsoft_agent_settings.json obok logów — "
                "nie udostępniaj czatu. Nadpisuje .env tylko jeśli MICROSOFT_GRAPH_ACCESS_TOKEN jest pusty."
            ]
        tok = parts[1].strip()
        if tok.casefold().startswith("bearer "):
            tok = tok[7:].strip()
        if len(tok) < 30:
            return ["Token wygląda na zbyt krótki — sprawdź, czy wkleiłeś pełny access_token (JWT)."]
        save_merged_settings(config.audit_log_path, {"graph_access_token": tok})
        return [
            "Zapisano token Graph (runtime). Kolejne wywołania microsoft_* w MCP użyją go zamiast MSAL. "
            "Wylogowanie: /microsoft-logout (czyści też ten token)."
        ]

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
            "Microsoft: za chwilę dostaniesz wiadomość z linkiem i kodem.\n"
            "— Otwórz wyłącznie adres z tej wiadomości (np. https://microsoft.com/devicelogin), wpisz kod, dokończ logowanie.\n"
            "— Nie wklejaj i nie otwieraj ręcznie adresu …/oauth2/nativeclient — to adres zwrotny OAuth, nie strona logowania; "
            "wejście tam bez pełnego łańcucha logowania daje błąd o braku response_type.\n"
            "— Jeśli tak jest: spróbuj Edge lub Chrome (Brave czasem obcina parametry URL) albo wyłącz blokady dla microsoft.com i login.microsoftonline.com.\n"
            "W Azure: Allow public client flows oraz jeden redirect Mobile/desktop zgodny z tenantem (/microsoft-show-settings).\n"
            "Może to potrwać kilka minut."
        ]

    if command_base in {"/microsoft-logout", "/msft-logout"}:
        cfg = load_config()
        save_merged_settings(cfg.audit_log_path, {"graph_access_token": None})
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
    run_telegram_startup_hooks(config)

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
