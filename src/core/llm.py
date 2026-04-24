from __future__ import annotations

import json
from typing import Any, Callable

from openai import OpenAI

from .mcp_tools import load_mcp_tools, run_mcp_tool
from .session_context import get_session_store
from .types import AgentConfig

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

MCP_AGENT_SYSTEM = """You are the jarvis1net assistant with tools provided by the MCP server.

Rules:
- Use previous turns from this session as conversation context.
- When the user asks which MCP tools exist, what tools are available, or for an up-to-date tool list, **always call `mcp_refresh_tool_manifest` first** and answer only from that tool output (never from memory, never from earlier turns). The host app may also force that tool call before your first reply.
- When the user asks for server-side file operations (list/read/write/create/delete/rename), **use tools** instead of guessing filesystem content.
- For diagnostics like disk usage, memory, load, uptime, or ping checks, use the shell diagnostic tool.
- Start with `fs_list_directory` or `fs_stat_path` when path structure is uncertain, then use `fs_read_file` / `fs_write_file` / others.
- Use `create_parents: true` when writing into a directory tree that may not exist yet.
- After tool calls, summarize clearly what was done, which paths were used, and any HTTP/tool errors.
- Do not claim an operation was performed unless you actually executed the appropriate tool.
- For Microsoft mailbox/calendar/OneDrive (`microsoft_*` tools), if tools report missing Graph token, tell the user to run **/microsoft-set-client** (paste Azure Client ID) then **/microsoft-login** in Telegram, or set env vars on the agent host.
- For mail/calendar/OneDrive **create, update, delete, send**, prefer **`microsoft_graph_api`** with the correct Graph `path` and `method` (see Microsoft Graph REST docs); helper tools only cover simple reads/lists.
- For **bulk** Graph reads (many folders/messages), use small ``$top`` (e.g. 25–50), ``$select`` with only needed fields, and iterate in steps — each tool JSON is **truncated** if too large, so prefer narrow queries over one giant response.
"""


def _user_requests_mcp_tool_catalog(text: str) -> bool:
    """Heuristic: user wants the current MCP tool list (not a filesystem path listing)."""
    t = text.casefold()
    needles = (
        "jakie narz",
        "jakie tool",
        "jakie toole",
        "lista narzed",
        "lista narzęd",
        "lista tool",
        "dostepne narzed",
        "dostępne narzęd",
        "ktore narzed",
        "które narzęd",
        "co za narzed",
        "co za narzęd",
        "pokaz narzed",
        "pokaż narzęd",
        "wylistuj narzed",
        "wylistuj narzęd",
        "what tools",
        "which tools",
        "list tools",
        "tool list",
        "available tools",
        "mcp tools",
        "narzedzia mcp",
        "narzędzia mcp",
        "manifest narzed",
        "manifest narzęd",
        "manifest tool",
        "mcp_refresh_tool_manifest",
    )
    return any(n in t for n in needles)


def _manifest_tool_in_schema_list(mcp_tools: list[dict[str, Any]]) -> bool:
    for item in mcp_tools:
        fn = item.get("function")
        if isinstance(fn, dict) and fn.get("name") == "mcp_refresh_tool_manifest":
            return True
    return False


def normalize_model_name(model: str) -> str:
    if "/" not in model:
        return f"openai/{model}"
    return model


def _simple_responses_reply(user_input: str, model: str, config: AgentConfig) -> str:
    client = OpenAI(
        api_key=config.openrouter_api_key,
        base_url=OPENROUTER_BASE_URL,
    )
    try:
        response = client.responses.create(
            model=normalize_model_name(model),
            input=user_input,
            max_output_tokens=220,
        )
        text = response.output_text.strip()
    except Exception as exc:
        return (
            "Model did not return a response (for example, token/credit limits). "
            f"Details: {exc}"
        )
    if not text:
        return (
            "The intent is unclear. Please specify the goal, for example a file path or directory."
        )
    return text


def _truncate_tool_result_for_context(text: str, max_chars: int) -> str:
    """Ogranicza JSON z MCP/Graph w kontekście modelu (jedna odpowiedź = jedna wiadomość tool)."""
    text = text.strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    note = (
        f"\n\n[_jarvis1net: obcięto wynik narzędzia do {max_chars} znaków (było {len(text)}). "
        "Użyj mniejszego $top i węższego $select w zapytaniach Graph.]"
    )
    head = max(500, max_chars - len(note))
    return text[:head] + note


def _format_mcp_tool_round(tool_calls: Any) -> str:
    """Text shown to the user before executing an MCP tool round."""
    lines = ["Using mcp-jarvis1net"]
    for tc in tool_calls:
        name = tc.function.name
        raw = (tc.function.arguments or "").strip() or "{}"
        if len(raw) > 1200:
            raw = raw[:1200] + "…"
        lines.append(f"  → {name}")
        lines.append(f"     {raw}")
    return "\n".join(lines)


def _chat_tool_loop(
    user_input: str,
    model: str,
    config: AgentConfig,
    *,
    prior_messages: list[dict[str, str]],
    before_tool_round: Callable[[str], None] | None = None,
) -> str:
    client = OpenAI(
        api_key=config.openrouter_api_key,
        base_url=OPENROUTER_BASE_URL,
    )
    model_id = normalize_model_name(model)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": MCP_AGENT_SYSTEM},
        *prior_messages,
        {"role": "user", "content": user_input},
    ]
    max_rounds = config.mcp_max_tool_rounds
    try:
        mcp_tools = load_mcp_tools(config)
    except Exception as exc:
        return f"MCP tools manifest error: {exc}"
    if not mcp_tools:
        return "No MCP tools are available for this API key."

    pending_force_manifest = (
        _user_requests_mcp_tool_catalog(user_input) and _manifest_tool_in_schema_list(mcp_tools)
    )

    for _ in range(max_rounds):
        try:
            tool_choice: Any = "auto"
            if pending_force_manifest:
                tool_choice = {"type": "function", "function": {"name": "mcp_refresh_tool_manifest"}}
                pending_force_manifest = False
            completion = client.chat.completions.create(
                model=model_id,
                messages=messages,
                tools=mcp_tools,
                tool_choice=tool_choice,
                max_tokens=4096,
            )
        except Exception as exc:
            return f"Model call error (chat + tools): {exc}"

        choice = completion.choices[0]
        msg = choice.message

        if choice.finish_reason == "tool_calls" and msg.tool_calls:
            if before_tool_round is not None:
                try:
                    before_tool_round(_format_mcp_tool_round(msg.tool_calls))
                except Exception:
                    pass
            assistant_payload: dict[str, Any] = {
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"},
                    }
                    for tc in msg.tool_calls
                ],
            }
            messages.append(assistant_payload)

            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = run_mcp_tool(tc.function.name, args, config)
                clipped = _truncate_tool_result_for_context(result, config.mcp_tool_result_max_chars)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": clipped})
            continue

        text = (msg.content or "").strip()
        if text:
            return text
        return "Model finished without text output. Please clarify the request."

    return (
        f"Tool round limit exceeded ({max_rounds}). "
        "Split the request into smaller steps or narrow down paths."
    )


def get_llm_reply(
    user_input: str,
    model: str,
    config: AgentConfig,
    *,
    session_key: str = "default",
    before_tool_round: Callable[[str], None] | None = None,
) -> str:
    if not config.openrouter_api_key.strip():
        return (
            "Missing OPENROUTER_API_KEY in .env. Add it to enable OpenRouter responses."
        )

    if config.mcp_api_key.strip():
        store = get_session_store(config.session_context_path)
        state = store.session(session_key)
        prior = state.chat.as_openai_messages()
        try:
            reply = _chat_tool_loop(
                user_input,
                model,
                config,
                prior_messages=prior,
                before_tool_round=before_tool_round,
            )
            state.chat.append_turn(user_input, reply)
            return reply
        finally:
            store.save()

    return _simple_responses_reply(user_input, model, config)
