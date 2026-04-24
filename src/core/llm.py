from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import parse_qsl, unquote

from openai import OpenAI

from .mcp_tools import (
    filter_mcp_tools_when_graph_token_present,
    load_mcp_tools,
    mcp_can_use_tools,
    run_mcp_tool,
)
from .openrouter_pricing import build_compact_token_usage_footer
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
- **Calendar (incl. all-day):** for **one named calendar day** use **`microsoft_calendar_events_on_date`** once with **`date=YYYY-MM-DD`** and optional **`time_zone`** (IANA, e.g. `Europe/Warsaw`) — **do not** fire many `microsoft_graph_api` GETs with tweaked URLs. For rolling “next weeks” use **`microsoft_calendar_list_events`**. If you must use **`microsoft_graph_api`**, a **single** `GET /me/calendarView` with **`startDateTime`** / **`endDateTime`** (camelCase) is enough; **avoid** `/me/events?$filter=start/dateTime…` for mixed all-day + timed lists. **At most one** successful calendar list per user question unless paging **`@odata.nextLink`**.
- **Calendar day = start only:** when summarizing which **calendar day** an event belongs to, use **`start`** (or tool field **`_jarvis1net_calendar_date`**). **Do not** move an event to the next day because **`end`** crosses midnight — Graph all-day entries often end at **next** day 00:00 while still being “that Tuesday”.
- **Inbox + subfolders:** When the user asks to **check the inbox / mailbox / latest mail** (all messages, any read state) without naming one folder, call **`microsoft_mail_list_inbox_tree`**. For **unread only** across Inbox + first-level subfolders, call **`microsoft_mail_list_unread_inbox_tree`**. Do **not** rely on **`microsoft_mail_list_messages`** alone (root Inbox only). For deeper nesting, use ``GET /me/mailFolders/{id}/childFolders`` then messages per id.
- **Mail search by topic / keywords:** When the user wants mail **matching words or meaning** across the mailbox (e.g. Otodom, Białołęka, mieszkanie), call **`microsoft_mail_search_messages`** with **query** built from their phrasing (Polish is fine). Use **subject**, **from**, and **bodyPreview** in **value** to judge which rows match; present only those you conclude are relevant. Fetch **full body** with **`microsoft_graph_api`** GET ``/me/messages/{id}`` only for messages where preview is not enough.
- **Mail list / “show last N messages” (folder browse, not $search):** use GET on messages with ``$select=id,subject,receivedDateTime,from`` (``from`` gives sender address/name). **Do not** add ``bodyPreview``, ``body``, or ``uniqueBody`` until the user explicitly asks for body content — otherwise tool output is often truncated and later messages are lost. Set ``$top`` to the requested count (e.g. last two → ``$top=2``). (**Exception:** **`microsoft_mail_search_messages`** intentionally returns **bodyPreview** so you can filter search hits.)
- To **mark many messages read**, after a GET with `value` of unread messages call **`microsoft_mail_mark_read`** with **all** `value[].id` strings in one `message_ids` array (repeat for next pages). Do **not** answer “all marked” after a single `PATCH` unless `value` had exactly one item or you used `microsoft_mail_mark_read` covering every id.
- For **bulk** Graph reads (many folders/messages), use small ``$top`` (e.g. 25–50), ``$select`` with only needed fields, and iterate in steps — each tool JSON is **truncated** if too large, so prefer narrow queries over one giant response.
- **Mailbox bulk work (step-by-step in one reply):** do **not** use deep ``$expand=childFolders`` on large trees in the same round as listing all message bodies. Prefer ``GET /me/mailFolders/inbox/childFolders?$select=id,displayName,unreadItemCount`` **without** expand. To **mark all unread in that folder** use **`microsoft_mail_mark_folder_read`** with that folder’s `mail_folder_id` (server follows **@odata.nextLink** — **never** use ``$skip`` on message lists; Graph ignores or mishandles it). For several folders with unread counts, call **`microsoft_mail_mark_folder_read` once per folder id** (few tool calls per round).
- **Never** issue the same tool with the **same arguments** twice in one turn: if a result was truncated, narrow ``$select``/``$top`` or query the **next** folder id from an earlier response instead of repeating the identical GET.
- **Mark many messages read/unread (or bulk PATCH on messages):** use ``$select=id`` only and ``$top`` at most **20** per GET when you list ids manually; follow **``@odata.nextLink``** only (no ``$skip`` on ``/messages``). Prefer **`microsoft_mail_mark_folder_read`** over hand-paged GETs so no id is missed. One huge GET can be truncated and you lose ids — then you cannot finish the job in one reply.
- **Mark one message read in Graph:** ``PATCH`` path ``/me/messages/{id}`` or ``/me/mailFolders/{folderId}/messages/{id}`` with JSON body exactly ``{"isRead": true}`` (boolean, key name ``isRead``). Success from the tool is usually ``{"ok": true, "status_code": 204}``. If the user says Outlook still shows unread, you may ``GET`` the same ``/me/messages/{id}?$select=isRead`` and report that JSON; do not claim success without that PATCH result in the tool output.
"""


def _mcp_system_message(config: AgentConfig) -> str:
    """System prompt + optional IANA timezone for quoting Graph times (UTC → local)."""
    tz = (config.display_timezone or "").strip()
    if not tz:
        return MCP_AGENT_SYSTEM
    return (
        MCP_AGENT_SYSTEM
        + f"\n- **User timezone (IANA: `{tz}`):** Microsoft Graph often returns UTC (trailing `Z`). "
        f"When you quote or summarize mail/calendar times for the user, convert to this zone "
        f"and state once that times are local ({tz}); 24h format unless the user asks otherwise."
    )


def _user_requests_mcp_tool_catalog(text: str) -> bool:
    """Heuristic: user wants the current MCP tool list (not a filesystem path listing)."""
    t = text.casefold()
    needles = (
        "what tools",
        "which tools",
        "list tools",
        "tool list",
        "available tools",
        "mcp tools",
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
    foot = _usage_footer_from_responses_api(
        response, model_id=normalize_model_name(model), config=config
    )
    return text + foot if foot else text


def _usage_footer_cumulative(
    prompt: int,
    completion: int,
    rounds: int,
    *,
    model_id: str,
    config: AgentConfig,
    limit_hit: bool = False,
) -> str:
    if rounds <= 0:
        return ""
    if prompt == 0 and completion == 0:
        return (
            "\n\n- Tokens: no usage field in API responses (OpenRouter sometimes omits usage)."
            + (
                " Hit MCP_MAX_TOOL_ROUNDS — raise it in .env (e.g. 24) or split the task."
                if limit_hit
                else ""
            )
        )
    return build_compact_token_usage_footer(
        api_key=config.openrouter_api_key,
        model_id=model_id,
        prompt_tokens=prompt,
        completion_tokens=completion,
        model_rounds=rounds,
        show_cost_estimate=config.openrouter_show_cost_estimate,
        limit_hit=limit_hit,
    )


def _usage_footer_from_responses_api(response: Any, *, model_id: str, config: AgentConfig) -> str:
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
    return build_compact_token_usage_footer(
        api_key=config.openrouter_api_key,
        model_id=model_id,
        prompt_tokens=pt,
        completion_tokens=ct,
        model_rounds=1,
        show_cost_estimate=config.openrouter_show_cost_estimate,
        limit_hit=False,
    )


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
    """Clamp MCP/Graph JSON in model context (one tool response = one tool message)."""
    text = text.strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    note = (
        f"\n\n[_jarvis1net: tool result truncated to {max_chars} chars (was {len(text)}). "
        "For mail lists: $select=id, $top≤20 per page, then PATCH each id before fetching the next page.]"
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
    The model often repeats GET .../messages?$filter=isRead eq false changing only $top (8 vs 20) —
    same list at the same time; treat as one query (until PATCH).
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


def _canonical_graph_query_param_key_for_dedupe(key: str) -> str:
    k = str(key).strip()
    if k.startswith("$"):
        return "$" + k[1:].lower()
    low = k.lower()
    if low == "startdatetime":
        return "startDateTime"
    if low == "enddatetime":
        return "endDateTime"
    return k


def _graph_calendar_query_dict_from_graph_api_args(args: dict[str, Any]) -> dict[str, str]:
    path = str(args.get("path", "")).strip()
    q: dict[str, str] = {}
    if "?" in path:
        _, qs = path.split("?", 1)
        for kk, vv in parse_qsl(unquote(qs), keep_blank_values=True):
            ck = _canonical_graph_query_param_key_for_dedupe(kk)
            q[ck] = str(vv)
    raw_q = args.get("query")
    if isinstance(raw_q, dict):
        for kk, vv in raw_q.items():
            ck = _canonical_graph_query_param_key_for_dedupe(str(kk))
            q[ck] = str(vv)
    return q


def _parse_graph_datetime_iso(value: str) -> datetime | None:
    s = unquote(value.strip())
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _graph_calendarview_loose_key(name: str, args: dict[str, Any]) -> str | None:
    if name != "microsoft_graph_api":
        return None
    if str(args.get("method", "GET")).strip().upper() != "GET":
        return None
    path = str(args.get("path", "")).strip()
    if "calendarview" not in path.casefold():
        return None
    q = _graph_calendar_query_dict_from_graph_api_args(args)
    st = q.get("startDateTime")
    en = q.get("endDateTime")
    if not st or not en:
        return None
    ds = _parse_graph_datetime_iso(st)
    de = _parse_graph_datetime_iso(en)
    if ds is None or de is None:
        return None
    ds = ds.astimezone(timezone.utc)
    de = de.astimezone(timezone.utc)
    return f"mg:calview:{ds.isoformat()}:{de.isoformat()}"


def _microsoft_calendar_day_loose_key(name: str, args: dict[str, Any]) -> str | None:
    if name != "microsoft_calendar_events_on_date":
        return None
    date_s = str(args.get("date", "")).strip()
    tz_s = str(args.get("time_zone", "Europe/Warsaw")).strip() or "Europe/Warsaw"
    if not date_s:
        return None
    return f"ms:calday:{date_s}:{tz_s}"


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
        return "No MCP tools are available (configure MCP stdio args or HTTP MCP_API_KEY)."

    pending_force_manifest = (
        _user_requests_mcp_tool_catalog(user_input) and _manifest_tool_in_schema_list(mcp_tools)
    )

    # Same tool call (e.g. identical Graph GET) is not sent to MCP twice in one user reply —
    # avoids loops when output was truncated or folder list was skipped.
    tool_result_cache: dict[str, str] = {}
    # Unread GET: same folder+filter+page, different $top → no second HTTP; after PATCH generation bumps.
    graph_read_generation = 0
    graph_unread_soft_cache: dict[str, tuple[str, int]] = {}
    graph_cal_soft_cache: dict[str, str] = {}
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
                + _usage_footer_cumulative(
                    usage_prompt,
                    usage_completion,
                    model_rounds,
                    model_id=model_id,
                    config=config,
                )
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
                    cal_key = _graph_calendarview_loose_key(name, args) or _microsoft_calendar_day_loose_key(
                        name, args
                    )
                    if cal_key is not None and cal_key in graph_cal_soft_cache:
                        raw = graph_cal_soft_cache[cal_key]
                        dup_note = (
                            "\n\n[_jarvis1net: same calendar window (start/end UTC or same date+zone) "
                            "already fetched — reuse previous JSON; do not repeat GET calendarView.]"
                        )
                    else:
                        loose = _graph_unread_messages_loose_key(name, args)
                        if loose is not None:
                            prev = graph_unread_soft_cache.get(loose)
                            if prev is not None and prev[1] == graph_read_generation:
                                raw = prev[0]
                                dup_note = (
                                    "\n\n[_jarvis1net: repeat unread GET in same folder+filter "
                                    "(e.g. different $top) — same list. Use microsoft_mail_mark_read for each id in value "
                                    "or microsoft_mail_mark_folder_read(mail_folder_id). Next page: @odata.nextLink only, not $skip.]"
                                )
                            else:
                                raw = run_mcp_tool(name, args, config)
                                tool_result_cache[sig] = raw
                                graph_unread_soft_cache[loose] = (raw, graph_read_generation)
                        elif sig in tool_result_cache:
                            raw = tool_result_cache[sig]
                            dup_note = (
                                "\n\n[_jarvis1net: identical call already ran — do not repeat. "
                                "If data was missing (truncation), narrow $select/$top or use subfolder id from earlier childFolders.]"
                            )
                        else:
                            raw = run_mcp_tool(name, args, config)
                            tool_result_cache[sig] = raw
                    if cal_key is not None:
                        graph_cal_soft_cache[cal_key] = raw
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
                    graph_cal_soft_cache.clear()
                    for k in list(tool_result_cache.keys()):
                        if k.startswith("microsoft_graph_api\0") or k.startswith(
                            "microsoft_mail_mark_read\0"
                        ) or k.startswith("microsoft_mail_mark_folder_read\0"):
                            tool_result_cache.pop(k, None)
            continue

        text = (msg.content or "").strip()
        foot = _usage_footer_cumulative(
            usage_prompt, usage_completion, model_rounds, model_id=model_id, config=config
        )
        if text:
            return text + foot
        fr = getattr(choice, "finish_reason", None)
        if fr == "length":
            return (
                "Model hit the response length limit (max_tokens) — often with very long "
                "tool calls or huge Graph JSON. Try a narrower request (e.g. one folder / mark read in one address) "
                "or continue with “more”. "
                "Admin can raise MCP_CHAT_COMPLETION_MAX_TOKENS or narrow mail list scope."
                + foot
            )
        return (
            "Model returned no text (empty content). Try a shorter prompt or split the task into steps."
            + foot
        )

    return (
        f"Tool round limit exceeded ({max_rounds}). "
        "Split the request into smaller steps or narrow down paths."
        + _usage_footer_cumulative(
            usage_prompt,
            usage_completion,
            model_rounds,
            model_id=model_id,
            config=config,
            limit_hit=True,
        )
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
            "Brak klucza OpenRouter. Ustaw go **trwale** jednym z sposobów:\n"
            "• W Telegramie: **/jarvis-set-openrouter-key** `sk-or-v1-...` (klucz z https://openrouter.ai/keys ) — "
            "zapisuje się na serwerze (w Dockerze w wolumenie /app/data, przetrwa restart).\n"
            "• Albo w pliku **.env** na hoście: `OPENROUTER_API_KEY=...` i restart kontenera.\n"
            "Potem napisz ponownie. **/jarvis-config-check** — podgląd konfiguracji."
        )

    if mcp_can_use_tools(config):
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
