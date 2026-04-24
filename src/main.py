import os
from pathlib import Path

from core.agent import run_agent_turn
from core.audit import write_audit_event
from core.config import load_config
from core.llm import get_llm_reply
from core.microsoft_auth import clear_token_cache_file, run_device_code_login
from core.microsoft_runtime_settings import (
    clear_settings_file,
    read_settings,
    save_merged_settings,
    settings_path,
    validate_client_id,
)
from core.session_context import get_session_store

_CLEAR_HISTORY_PHRASES = frozenset(
    {
        "clear history",
        "clear chat history",
        "reset chat",
        "start over",
    }
)


def main() -> None:
    print("jarvis1net v0.1 — type naturally. Use /exit to quit. Type 'clear history' to reset chat memory.")
    print()

    while True:
        try:
            line = input("You> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not line:
            continue
        if line.lower() == "/exit":
            break

        config = load_config()
        low = line.lower()
        cmd = low.split()[0] if low else ""
        stripped = line.strip()

        if cmd == "/microsoft-set-client":
            parts = stripped.split()
            if len(parts) < 2:
                print("Użycie: /microsoft-set-client <Client-ID> [tenant]\n")
                continue
            cid = parts[1].strip()
            tenant = parts[2].strip() if len(parts) > 2 else "organizations"
            if not validate_client_id(cid):
                print("Client ID musi być pełnym UUID z Azure.\n")
                continue
            save_merged_settings(config.audit_log_path, {"client_id": cid, "tenant_id": tenant})
            print(f"Zapisano (tenant: {tenant}). Teraz /microsoft-login\n")
            continue

        if cmd == "/microsoft-set-tenant":
            parts = stripped.split()
            if len(parts) < 2:
                print(
                    "Użycie: /microsoft-set-tenant <consumers|organizations|common|GUID>\n"
                )
                continue
            raw = parts[1].strip()
            t = raw.casefold()
            ok = t in ("common", "organizations", "consumers") or validate_client_id(raw)
            if not ok:
                print("Nieznany tenant.\n")
                continue
            save_merged_settings(config.audit_log_path, {"tenant_id": raw})
            print(f"Zapisano tenant: {raw}\n")
            continue

        if cmd in {"/microsoft-set-scopes", "/microsoft-scopes"}:
            parts = stripped.split(None, 1)
            if len(parts) < 2 or not parts[1].strip():
                print(" ".join(config.microsoft_graph_scopes))
                print("Użycie: /microsoft-set-scopes User.Read Mail.Read …\n")
                continue
            scope_list = [s.strip() for s in parts[1].replace(",", " ").split() if s.strip()]
            if not scope_list:
                print("Podaj scope.\n")
                continue
            save_merged_settings(config.audit_log_path, {"graph_scopes": scope_list})
            print(f"Zapisano {len(scope_list)} scope(y).\n")
            continue

        if cmd in {"/jarvis-limits", "/mcp-limits", "/limits"}:
            print("jarvis1net — limity MCP:")
            print(f"  MCP_MAX_TOOL_ROUNDS: {config.mcp_max_tool_rounds}")
            print(f"  MCP_TOOL_RESULT_MAX_CHARS: {config.mcp_tool_result_max_chars}")
            print(f"  MCP_MICROSOFT_TOOL_RESULT_MAX_CHARS: {config.mcp_microsoft_tool_result_max_chars}")
            print(f"  MCP_CHAT_COMPLETION_MAX_TOKENS: {config.mcp_chat_completion_max_tokens}")
            print(f"  MCP_TIMEOUT_SEC: {config.mcp_timeout_sec}")
            print(f"  DISPLAY_TIMEZONE: {config.display_timezone or '(brak)'}")
            print()
            continue

        if cmd in {"/microsoft-show-settings", "/microsoft-config"}:
            rt = read_settings(config.audit_log_path)
            cid_env = os.getenv("MICROSOFT_CLIENT_ID", "").strip()
            src = "env" if cid_env else ("plik" if rt.get("client_id") else "brak")
            has_cache = Path(config.microsoft_token_cache_path).expanduser().exists()
            ten_env = os.getenv("MICROSOFT_TENANT_ID", "").strip()
            ten_rt = str(rt.get("tenant_id") or "").strip()
            if ten_rt:
                ten_src = "plik (nadpisuje .env)"
            elif ten_env:
                ten_src = "env"
            else:
                ten_src = "domyślnie organizations"
            tok_env = bool(os.getenv("MICROSOFT_GRAPH_ACCESS_TOKEN", "").strip())
            tok_rt = bool(
                isinstance(rt.get("graph_access_token"), str) and str(rt.get("graph_access_token")).strip()
            )
            if tok_env:
                tok_src = "env MICROSOFT_GRAPH_ACCESS_TOKEN"
            elif tok_rt:
                tok_src = "plik graph_access_token"
            else:
                tok_src = "MSAL po /microsoft-login"
            print(f"Client ID: {config.microsoft_client_id or '(brak)'} (źródło: {src})")
            print(f"Tenant: {config.microsoft_tenant_id} (źródło: {ten_src})")
            print(f"Scopes: {' '.join(config.microsoft_graph_scopes)}")
            print(f"Token Graph: {tok_src}")
            print(f"Ustawienia: {settings_path(config.audit_log_path)}")
            print(f"Cache tokenów: {'tak' if has_cache else 'nie'}\n")
            continue

        if cmd in {"/microsoft-set-graph-token", "/microsoft-paste-token"}:
            parts = stripped.split(None, 1)
            if len(parts) < 2 or not parts[1].strip():
                print(
                    "Użycie: /microsoft-set-graph-token <access_token>\n"
                    "np. wynik: az account get-access-token --resource https://graph.microsoft.com -o tsv\n"
                )
                continue
            tok = parts[1].strip()
            if tok.casefold().startswith("bearer "):
                tok = tok[7:].strip()
            if len(tok) < 30:
                print("Token zbyt krótki.\n")
                continue
            save_merged_settings(config.audit_log_path, {"graph_access_token": tok})
            print("Zapisano graph_access_token (runtime).\n")
            continue

        if cmd in {"/microsoft-clear-runtime", "/microsoft-clear-settings"}:
            print(clear_settings_file(config.audit_log_path))
            print()
            continue

        if cmd in {"/microsoft-login", "/msft-login"}:
            if not config.microsoft_client_id.strip():
                print("Brak Client ID — użyj /microsoft-set-client <UUID> lub .env\n")
                continue
            try:

                def _n(msg: str) -> None:
                    print(f"\n{msg}\n")

                print(run_device_code_login(config, notify=_n))
            except Exception as exc:
                print(f"Błąd logowania Microsoft: {exc}\n")
            continue
        if cmd in {"/microsoft-logout", "/msft-logout"}:
            save_merged_settings(config.audit_log_path, {"graph_access_token": None})
            print(clear_token_cache_file(config))
            print()
            continue

        if low in _CLEAR_HISTORY_PHRASES:
            st = get_session_store(config.session_context_path)
            st.clear_key("cli")
            st.save()
            print("\nCLI chat history cleared.\n")
            continue

        response = run_agent_turn(line, config)
        llm_text = get_llm_reply(
            user_input=line,
            model=response.selected_model,
            config=config,
            session_key="cli",
            before_tool_round=lambda msg: print(f"\n{msg}\n"),
        )
        write_audit_event(
            log_path=config.audit_log_path,
            event_type="chat_response",
            payload={
                "model": response.selected_model,
                "trigger": line,
            },
        )
        print()
        print("Model response:")
        print(llm_text)
        print()


if __name__ == "__main__":
    main()
