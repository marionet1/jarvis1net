import threading
import time
from typing import Any, Callable

import requests

from core.agent import run_agent_turn
from core.audit import write_audit_event
from core.config import load_config
from core.llm import get_llm_reply
from core.microsoft_auth import clear_token_cache_file, run_device_code_login
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
            "Microsoft (Graph / skrzynka): ustaw MICROSOFT_CLIENT_ID w .env, potem wyślij /microsoft-login — bot poda link i kod do wpisania w przeglądarce."
        ]

    if command_base in {"/microsoft-login", "/msft-login"}:
        cfg = load_config()
        if not cfg.microsoft_client_id.strip():
            return [
                "Brak MICROSOFT_CLIENT_ID w .env agenta. Dodaj Application (client) ID z Azure Portal "
                "(typ: public client / device code) i zrestartuj bota."
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
