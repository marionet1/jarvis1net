"""Sprawdzenie konfiguracji po starcie: wymagane klucze, MCP health, opcjonalnie Graph."""

from __future__ import annotations

from dataclasses import dataclass, field

from .jarvis_runtime_settings import clear_jarvis_runtime_file
from .mcp_tools import mcp_health
from .microsoft_auth import clear_token_cache_file, resolve_graph_access_token
from .microsoft_runtime_settings import clear_settings_file
from .session_context import get_session_store
from .types import AgentConfig


@dataclass
class StartupCheckResult:
    """`blocking` puste = można gadać z LLM. `mcp_summary` / `graph_summary` — krótki podgląd do raportu OK."""

    ok: bool
    mcp_summary: str = ""
    graph_summary: str = ""
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def run_startup_checks(config: AgentConfig) -> StartupCheckResult:
    blocking: list[str] = []
    warnings: list[str] = []

    if not config.mcp_api_key.strip():
        mcp_summary = "wyłączone (brak MCP_API_KEY)"
    else:
        try:
            mcp_health(config)
            mcp_summary = f"{config.mcp_server_url} — OK"
        except Exception as exc:
            mcp_summary = f"{config.mcp_server_url} — błąd ({type(exc).__name__})"
            warnings.append(
                f"MCP: {str(exc)[:200]}. Wklej poprawny klucz: /jarvis-set-mcp-key <klucz>"
            )

    tok = resolve_graph_access_token(config)
    if tok:
        graph_summary = "OK"
    elif not config.microsoft_client_id.strip():
        graph_summary = "brak"
        warnings.append(
            "Graph: brak tokenu i Client ID — /microsoft-set-client <UUID> potem /microsoft-login "
            "albo /microsoft-set-graph-token"
        )
    else:
        graph_summary = "czeka na logowanie (/microsoft-login)"

    if not config.openrouter_api_key.strip():
        blocking.append(
            "OPENROUTER_API_KEY — brak. W czacie: /jarvis-set-openrouter-key <klucz> (zapis obok logów; wyciek na kanale możliwy)."
        )

    if not config.telegram_allowed_chat_ids:
        warnings.append(
            "TELEGRAM_ALLOWED_CHAT_IDS puste — każdy z linkiem może pisać; produkcja: ustaw listę chat_id w .env."
        )

    return StartupCheckResult(
        ok=len(blocking) == 0,
        mcp_summary=mcp_summary,
        graph_summary=graph_summary,
        blocking=blocking,
        warnings=warnings,
    )


def format_startup_report_plain(result: StartupCheckResult, *, title: str = "Konfiguracja jarvis1net") -> str:
    """Krótki tekst do Telegrama / logów."""
    if result.ok:
        lines = [
            title,
            "",
            "Konfiguracja OK.",
            f"MCP: {result.mcp_summary}",
            f"Microsoft Graph: {result.graph_summary}",
        ]
        for w in result.warnings:
            lines.append(f"(uwaga) {w}")
        return "\n".join(lines)

    lines = [title, "", "Konfiguracja niepełna — dopisz brakujące:"]
    for b in result.blocking:
        lines.append(f"- {b}")
    for w in result.warnings:
        lines.append(f"- {w}")
    lines.append("")
    lines.append(
        "Z czatu (świadomie ryzykowne): /jarvis-set-openrouter-key …, /jarvis-set-mcp-key …; "
        "Microsoft: /microsoft-set-client + /microsoft-login lub /microsoft-set-graph-token. "
        "Wyczyść zapisane dane bota: /jarvis-config-reset."
    )
    return "\n".join(lines)


def reset_runtime_agent_state(config: AgentConfig) -> list[str]:
    """
    Czyści runtime z czatu + cache MSAL + pamięć rozmów + jarvis_runtime_secrets.json. Nie usuwa .env.
    """
    out: list[str] = []
    out.append(clear_settings_file(config.audit_log_path))
    out.append(clear_jarvis_runtime_file(config.audit_log_path))
    out.append(clear_token_cache_file(config))
    store = get_session_store(config.session_context_path)
    store.clear_all_sessions()
    store.save()
    out.append("Wyczyszczono pamięć rozmów (wszystkie sesje w session_paths.json).")
    return out
