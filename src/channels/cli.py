from core.audit import write_audit_event
from core.chat_phrases import CLEAR_HISTORY_PHRASES
from core.command_shared import (
    build_microsoft_settings_lines,
    parse_microsoft_set_client,
    parse_microsoft_set_graph_token,
    parse_microsoft_set_scopes,
    parse_microsoft_set_tenant,
)
from core.runtime_config import (
    format_startup_report_plain,
    load_config,
    reset_runtime_agent_state,
    run_startup_checks,
)
from core.jarvis_runtime_settings import save_merged_jarvis_runtime
from core.llm import get_llm_reply
from core.session_context import get_session_store
from integrations.microsoft import (
    clear_settings_file,
    clear_token_cache_file,
    run_device_code_login,
    save_merged_settings,
)


def main() -> None:
    print("jarvis1net v0.1 — type naturally. Use /exit to quit. Type 'clear history' to reset chat memory.")
    print()
    _cfg0 = load_config()
    print(format_startup_report_plain(run_startup_checks(_cfg0), title="jarvis1net — startup check (CLI)"))
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
            ok, error, payload = parse_microsoft_set_client(stripped)
            if not ok or payload is None:
                print(f"{error}\n")
                continue
            tenant = str(payload["tenant_id"])
            save_merged_settings(config.audit_log_path, payload)
            print(f"Saved (tenant: {tenant}). Next: /microsoft-login\n")
            continue

        if cmd == "/microsoft-set-tenant":
            ok, error, tenant = parse_microsoft_set_tenant(stripped)
            if not ok or tenant is None:
                print(f"{error}\n")
                continue
            save_merged_settings(config.audit_log_path, {"tenant_id": tenant})
            print(f"Saved tenant: {tenant}\n")
            continue

        if cmd in {"/microsoft-set-scopes", "/microsoft-scopes"}:
            ok, error, scopes = parse_microsoft_set_scopes(stripped)
            if scopes is None:
                print(" ".join(config.microsoft_graph_scopes))
                print("Usage: /microsoft-set-scopes User.Read Mail.Read …\n")
                continue
            if not ok or scopes is None:
                print(f"{error}\n")
                continue
            save_merged_settings(config.audit_log_path, {"graph_scopes": scopes})
            print(f"Saved {len(scopes)} scope(s).\n")
            continue

        if cmd in {"/jarvis-config-check", "/config-check"}:
            print(format_startup_report_plain(run_startup_checks(config), title="jarvis1net — config check (CLI)"))
            print()
            continue

        if cmd in {"/jarvis-config-reset", "/config-reset"}:
            for msg in reset_runtime_agent_state(config):
                print(msg)
            print()
            continue

        if cmd == "/jarvis-set-openrouter-key":
            parts = stripped.split(None, 1)
            key = parts[1].strip() if len(parts) > 1 else ""
            if not key:
                print("Usage: /jarvis-set-openrouter-key <key>\n")
                continue
            if len(key) < 12:
                print("Key too short.\n")
                continue
            save_merged_jarvis_runtime(config.audit_log_path, {"openrouter_api_key": key})
            print("Saved openrouter_api_key to jarvis_runtime_secrets.json\n")
            continue

        if cmd in {"/jarvis-limits", "/mcp-limits", "/limits"}:
            print("jarvis1net — MCP limits:")
            print(f"  MCP_STDIO: {config.mcp_stdio_command} {' '.join(config.mcp_stdio_args)}")
            print(f"  MCP_MAX_TOOL_ROUNDS: {config.mcp_max_tool_rounds}")
            print(f"  MCP_TOOL_RESULT_MAX_CHARS: {config.mcp_tool_result_max_chars}")
            print(f"  MCP_MICROSOFT_TOOL_RESULT_MAX_CHARS: {config.mcp_microsoft_tool_result_max_chars}")
            print(f"  MCP_CHAT_COMPLETION_MAX_TOKENS: {config.mcp_chat_completion_max_tokens}")
            print(f"  MCP_TIMEOUT_SEC: {config.mcp_timeout_sec}")
            print(f"  OPENROUTER_SHOW_COST_ESTIMATE: {1 if config.openrouter_show_cost_estimate else 0}")
            print(f"  DISPLAY_TIMEZONE: {config.display_timezone or '(none)'}")
            print()
            continue

        if cmd in {"/microsoft-show-settings", "/microsoft-config"}:
            for line_item in build_microsoft_settings_lines(config):
                print(line_item)
            print()
            continue

        if cmd in {"/microsoft-set-graph-token", "/microsoft-paste-token"}:
            ok, error, token = parse_microsoft_set_graph_token(stripped)
            if token is None:
                print(
                    "Usage: /microsoft-set-graph-token <access_token>\n"
                    "e.g. output of: az account get-access-token --resource https://graph.microsoft.com -o tsv\n"
                )
                continue
            if not ok or token is None:
                print(f"{error}\n")
                continue
            save_merged_settings(config.audit_log_path, {"graph_access_token": token})
            print("Saved graph_access_token (runtime).\n")
            continue

        if cmd in {"/microsoft-clear-runtime", "/microsoft-clear-settings"}:
            print(clear_settings_file(config.audit_log_path))
            print()
            continue

        if cmd in {"/microsoft-login", "/msft-login"}:
            if not config.microsoft_client_id.strip():
                print("No Client ID — use /microsoft-set-client <UUID> or runtime config\n")
                continue
            try:

                def _n(msg: str) -> None:
                    print(f"\n{msg}\n")

                print(run_device_code_login(config, notify=_n))
            except Exception as exc:
                print(f"Microsoft login error: {exc}\n")
            continue
        if cmd in {"/microsoft-logout", "/msft-logout"}:
            save_merged_settings(config.audit_log_path, {"graph_access_token": None})
            print(clear_token_cache_file(config))
            print()
            continue

        if low in CLEAR_HISTORY_PHRASES:
            st = get_session_store(config.session_context_path)
            st.clear_key("cli")
            st.save()
            print("\nCLI chat history cleared.\n")
            continue

        llm_text = get_llm_reply(
            user_input=line,
            model=config.model,
            config=config,
            session_key="cli",
            before_tool_round=lambda msg: print(f"\n{msg}\n"),
        )
        write_audit_event(
            log_path=config.audit_log_path,
            event_type="chat_response",
            payload={
                "model": config.model,
                "trigger": line,
            },
        )
        print()
        print("Model response:")
        print(llm_text)
        print()


if __name__ == "__main__":
    main()
