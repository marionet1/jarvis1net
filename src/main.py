from core.agent import run_agent_turn
from core.audit import write_audit_event
from core.config import load_config
from core.llm import get_llm_reply
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
    config = load_config()
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

        low = line.lower()
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
