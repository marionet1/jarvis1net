from __future__ import annotations

import json
from typing import Any, Callable
from urllib.parse import parse_qsl, unquote

from openai import OpenAI

from .mcp_tools import filter_mcp_tools_when_graph_token_present, load_mcp_tools, run_mcp_tool
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
- **Microsoft:** go straight to the concrete `microsoft_*` tool the user needs (for example `microsoft_graph_api` or `microsoft_graph_me`). Call **`microsoft_integration_status` only if it still appears in the tool list and you must diagnose missing Graph token** — never as a routine first step before every mail/calendar action.
- For mail/calendar/OneDrive **create, update, delete, send**, prefer **`microsoft_graph_api`** with the correct Graph `path` and `method` (see Microsoft Graph REST docs); helper tools only cover simple reads/lists.
- **Mail list / “pokaż ostatnie N maili” (bez czytania treści):** używaj GET na wiadomościach z ``$select=id,subject,receivedDateTime,from`` (pole ``from`` zwraca adres/nazwę nadawcy). **Nie** dodawaj ``bodyPreview``, ``body`` ani ``uniqueBody``, dopóki użytkownik wyraźnie nie poprosi o treść / odczytanie konkretnej wiadomości — inaczej wynik narzędzia bywa obcięty i giną kolejne maile. Ustaw ``$top`` równy liczbie prośby (np. dwa ostatnie → ``$top=2``).
- To **mark many messages read**, after a GET with `value` of unread messages call **`microsoft_mail_mark_read`** with **all** `value[].id` strings in one `message_ids` array (repeat for next pages). Do **not** answer “all marked” after a single `PATCH` unless `value` had exactly one item or you used `microsoft_mail_mark_read` covering every id.
- For **bulk** Graph reads (many folders/messages), use small ``$top`` (e.g. 25–50), ``$select`` with only needed fields, and iterate in steps — each tool JSON is **truncated** if too large, so prefer narrow queries over one giant response.
- **Mailbox bulk work (step-by-step in one reply):** do **not** use deep ``$expand=childFolders`` on large trees in the same round as listing all message bodies. Prefer ``GET /me/mailFolders/inbox/childFolders?$select=id,displayName,unreadItemCount`` **without** expand. For **„oznacz wszystkie nieprzeczytane w tym folderze”** use **`microsoft_mail_mark_folder_read`** with that folder’s `mail_folder_id` (server follows **@odata.nextLink** — **never** use ``$skip`` on message lists; Graph ignores or mishandles it). For several folders with unread counts, call **`microsoft_mail_mark_folder_read` once per folder id** (few tool calls per round).
- **Never** issue the same tool with the **same arguments** twice in one turn: if a result was truncated, narrow ``$select``/``$top`` or query the **next** folder id from an earlier response instead of repeating the identical GET.
- **Mark many messages read/unread (or bulk PATCH on messages):** use ``$select=id`` only and ``$top`` at most **20** per GET when you list ids manually; follow **``@odata.nextLink``** only (no ``$skip`` on ``/messages``). Prefer **`microsoft_mail_mark_folder_read`** over hand-paged GETs so no id is missed. One huge GET can be truncated and you lose ids — then you cannot finish the job in one reply.
- **Mark one message read in Graph:** ``PATCH`` path ``/me/messages/{id}`` or ``/me/mailFolders/{folderId}/messages/{id}`` with JSON body exactly ``{"isRead": true}`` (boolean, key name ``isRead``). Success from the tool is usually ``{"ok": true, "status_code": 204}``. If the user says Outlook still shows unread, you may ``GET`` the same ``/me/messages/{id}?$select=isRead`` and report that JSON; do not claim success without that PATCH result in the tool output.
"""


def _mcp_system_message(config: AgentConfig) -> str:
    """System prompt + opcjonalna strefa czasowa do cytowania czasów z Graph (UTC → lokalna)."""
    tz = (config.display_timezone or "").strip()
    if not tz:
        return MCP_AGENT_SYSTEM
    return (
        MCP_AGENT_SYSTEM
        + f"\n- **Strefa czasowa użytkownika (IANA: `{tz}`):** Microsoft Graph zwraca często UTC (końcówka `Z`). "
        f"Gdy cytujesz lub podsumowujesz daty/czasy maili i kalendarza dla użytkownika, przelicz je na tę strefę "
        f"i raz napisz, że to czas lokalny ({tz}); format 24h, o ile użytkownik nie prosi inaczej."
    )


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
    foot = _usage_footer_from_responses_api(response)
    return text + foot if foot else text


def _usage_footer_cumulative(prompt: int, completion: int, rounds: int, *, limit_hit: bool = False) -> str:
    if rounds <= 0:
        return ""
    total = prompt + completion
    if prompt == 0 and completion == 0:
        base = "\n\n— Tokeny: brak pola usage w odpowiedziach API (OpenRouter czasem nie zwraca usage)."
    else:
        base = (
            f"\n\n— Tokeny (łącznie w tej odpowiedzi, suma z {rounds} wywołań modelu): "
            f"prompt≈{prompt}, completion≈{completion}, razem≈{total}."
        )
    if limit_hit:
        base += " Osiągnięto MCP_MAX_TOOL_ROUNDS — ustaw wyższą wartość w .env (np. 24) lub podziel zadanie."
    return base


def _usage_footer_from_responses_api(response: Any) -> str:
    u = getattr(response, "usage", None)
    if u is None:
        return ""
    inp = getattr(u, "input_tokens", None)
    if inp is None:
        inp = getattr(u, "prompt_tokens", None)
    out = getattr(u, "output_tokens", None)
    if out is None:
        out = getattr(u, "completion_tokens", None)
    pt = int(inp or 0)
    ct = int(out or 0)
    tt = getattr(u, "total_tokens", None)
    total = int(tt) if tt is not None else pt + ct
    if pt == 0 and ct == 0 and total == 0:
        return ""
    return f"\n\n— Tokeny (ta odpowiedź, API): wejście≈{pt}, wyjście≈{ct}, suma≈{total}."


_MAX_GRAPH_VALUE_ITEMS = 22
_MAX_GRAPH_CHILD_FOLDERS_ITEMS = 12


def _shrink_microsoft_graph_json_payload(obj: Any) -> Any:
    """Reduces huge OData `value` / nested `childFolders` arrays before token-heavy tool messages."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k == "value" and isinstance(v, list) and len(v) > _MAX_GRAPH_VALUE_ITEMS:
                out[k] = [_shrink_microsoft_graph_json_payload(x) for x in v[:_MAX_GRAPH_VALUE_ITEMS]]
                out["_jarvis1net_truncated_value_total"] = len(v)
            elif k == "childFolders" and isinstance(v, list) and len(v) > _MAX_GRAPH_CHILD_FOLDERS_ITEMS:
                out[k] = [_shrink_microsoft_graph_json_payload(x) for x in v[:_MAX_GRAPH_CHILD_FOLDERS_ITEMS]]
                out["_jarvis1net_truncated_childFolders_total"] = len(v)
            else:
                out[k] = _shrink_microsoft_graph_json_payload(v)
        return out
    if isinstance(obj, list):
        return [_shrink_microsoft_graph_json_payload(x) for x in obj]
    return obj


def _maybe_shrink_microsoft_tool_json(name: str, raw: str) -> str:
    if not name.startswith("microsoft_"):
        return raw
    text = raw.strip()
    if not text.startswith("{"):
        return raw
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return raw
    if not isinstance(data, dict):
        return raw
    shrunk = _shrink_microsoft_graph_json_payload(data)
    try:
        return json.dumps(shrunk, ensure_ascii=False)
    except (TypeError, ValueError):
        return raw


def _tool_result_char_cap(name: str, config: AgentConfig) -> int:
    if name.startswith("microsoft_"):
        return min(config.mcp_tool_result_max_chars, config.mcp_microsoft_tool_result_max_chars)
    return config.mcp_tool_result_max_chars


def _truncate_tool_result_for_context(text: str, max_chars: int) -> str:
    """Ogranicza JSON z MCP/Graph w kontekście modelu (jedna odpowiedź = jedna wiadomość tool)."""
    text = text.strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    note = (
        f"\n\n[_jarvis1net: obcięto wynik narzędzia do {max_chars} znaków (było {len(text)}). "
        "Dla list maili: $select=id, $top≤20 na stronę, potem PATCH każdego id zanim pobierzesz kolejną stronę.]"
    )
    head = max(500, max_chars - len(note))
    return text[:head] + note


def _tool_call_signature(name: str, args: dict[str, Any]) -> str:
    try:
        payload = json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except TypeError:
        payload = str(args)
    return f"{name}\0{payload}"


def _graph_unread_messages_loose_key(name: str, args: dict[str, Any]) -> str | None:
    """
    Model często powtarza GET .../messages?$filter=isRead eq false zmieniając tylko $top (8 vs 20) —
    to ta sama lista w tym samym momencie; traktujemy jak jedno zapytanie (do czasu PATCH).
    """
    if name != "microsoft_graph_api":
        return None
    if str(args.get("method", "GET")).strip().upper() != "GET":
        return None
    raw_path = str(args.get("path", "")).strip()
    if "/messages" not in raw_path or "?" not in raw_path:
        return None
    base, q = raw_path.split("?", 1)
    try:
        pairs = dict(parse_qsl(unquote(q), keep_blank_values=True))
    except (TypeError, ValueError):
        return None
    filt = str(pairs.get("$filter") or pairs.get("filter") or "")
    if "isread" not in filt.casefold() or "false" not in filt.casefold():
        return None
    skip = str(pairs.get("$skip") or pairs.get("skip") or "")
    skiptoken = str(pairs.get("$skiptoken") or "")
    page = skip or skiptoken
    return f"mg:unread:{base}\0{filt}\0page:{page}"


def _graph_patch_to_message(path: str, method: str) -> bool:
    """True when PATCH targets a message resource (clears Graph GET soft-cache)."""
    if str(method).strip().upper() != "PATCH":
        return False
    pl = path.casefold()
    # /me/messages/{id} OR /me/mailFolders/.../messages/{id} (not only the latter)
    return "/messages/" in pl


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
        {"role": "system", "content": _mcp_system_message(config)},
        *prior_messages,
        {"role": "user", "content": user_input},
    ]
    max_rounds = config.mcp_max_tool_rounds
    try:
        mcp_tools = filter_mcp_tools_when_graph_token_present(config, load_mcp_tools(config))
    except Exception as exc:
        return f"MCP tools manifest error: {exc}"
    if not mcp_tools:
        return "No MCP tools are available for this API key."

    pending_force_manifest = (
        _user_requests_mcp_tool_catalog(user_input) and _manifest_tool_in_schema_list(mcp_tools)
    )

    # W jednej odpowiedzi na użytkownika: identyczne wywołanie (np. ten sam GET Graph) nie idzie drugi raz do MCP —
    # model często zapętla się, gdy wynik był obcięty lub nie przeczytał listy folderów.
    tool_result_cache: dict[str, str] = {}
    # GET nieprzeczytanych: ten sam folder+filter+strona, inne $top → bez ponownego HTTP; po PATCH generacja rośnie.
    graph_read_generation = 0
    graph_unread_soft_cache: dict[str, tuple[str, int]] = {}
    usage_prompt = 0
    usage_completion = 0
    model_rounds = 0

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
                max_tokens=config.mcp_chat_completion_max_tokens,
            )
        except Exception as exc:
            return (
                f"Model call error (chat + tools): {exc}"
                + _usage_footer_cumulative(usage_prompt, usage_completion, model_rounds)
            )

        model_rounds += 1
        u = getattr(completion, "usage", None)
        if u is not None:
            usage_prompt += int(getattr(u, "prompt_tokens", 0) or 0)
            usage_completion += int(getattr(u, "completion_tokens", 0) or 0)

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
                name = tc.function.name
                dup_note = ""
                if name == "mcp_refresh_tool_manifest":
                    raw = run_mcp_tool(name, args, config)
                else:
                    sig = _tool_call_signature(name, args)
                    loose = _graph_unread_messages_loose_key(name, args)
                    if loose is not None:
                        prev = graph_unread_soft_cache.get(loose)
                        if prev is not None and prev[1] == graph_read_generation:
                            raw = prev[0]
                            dup_note = (
                                "\n\n[_jarvis1net: ponowny GET nieprzeczytanych w tym samym folderze i filtrze "
                                "(np. inne $top) — to ta sama lista. Użyj microsoft_mail_mark_read na każde id z value "
                                "albo microsoft_mail_mark_folder_read(mail_folder_id). Następna strona: tylko @odata.nextLink, nie $skip.]"
                            )
                        else:
                            raw = run_mcp_tool(name, args, config)
                            tool_result_cache[sig] = raw
                            graph_unread_soft_cache[loose] = (raw, graph_read_generation)
                    elif sig in tool_result_cache:
                        raw = tool_result_cache[sig]
                        dup_note = (
                            "\n\n[_jarvis1net: to samo wywołanie już było — nie powtarzaj. "
                            "Jeśli brakowało danych (obcięcie), zawęż $select/$top albo użyj id podfolderu z wcześniejszej odpowiedzi childFolders.]"
                        )
                    else:
                        raw = run_mcp_tool(name, args, config)
                        tool_result_cache[sig] = raw
                shrunk = _maybe_shrink_microsoft_tool_json(name, raw)
                clipped = _truncate_tool_result_for_context(shrunk, _tool_result_char_cap(name, config))
                if dup_note:
                    clipped = clipped + dup_note
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": clipped})
                if name in ("microsoft_mail_mark_read", "microsoft_mail_mark_folder_read") or (
                    name == "microsoft_graph_api"
                    and _graph_patch_to_message(str(args.get("path", "")), str(args.get("method", "")))
                ):
                    graph_read_generation += 1
                    graph_unread_soft_cache.clear()
                    for k in list(tool_result_cache.keys()):
                        if k.startswith("microsoft_graph_api\0") or k.startswith(
                            "microsoft_mail_mark_read\0"
                        ) or k.startswith("microsoft_mail_mark_folder_read\0"):
                            tool_result_cache.pop(k, None)
            continue

        text = (msg.content or "").strip()
        foot = _usage_footer_cumulative(usage_prompt, usage_completion, model_rounds)
        if text:
            return text + foot
        fr = getattr(choice, "finish_reason", None)
        if fr == "length":
            return (
                "Model zatrzymał się na limicie długości odpowiedzi (max_tokens) — zwykle przy bardzo długich "
                "wywołaniach narzędzi lub ogromnym JSON z Graph. Napisz proszę węższe polecenie (np. tylko jeden "
                "folder / „oznacz przeczytane w controlek@gmail.com”) albo kontynuuj „dalej”. "
                "Administrator może zwiększyć MCP_CHAT_COMPLETION_MAX_TOKENS lub zmniejszyć zakres list maili."
                + foot
            )
        return (
            "Model nie zwrócił treści (pusty content). Spróbuj krótszego polecenia lub podziel zadanie na kroki."
            + foot
        )

    return (
        f"Tool round limit exceeded ({max_rounds}). "
        "Split the request into smaller steps or narrow down paths."
        + _usage_footer_cumulative(usage_prompt, usage_completion, model_rounds, limit_hit=True)
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
