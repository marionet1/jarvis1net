"""Sprawdzenie konfiguracji po starcie: wymagane klucze, MCP health, opcjonalnie Graph."""

from __future__ import annotations

from dataclasses import dataclass, field

from .mcp_tools import mcp_health
from .microsoft_auth import clear_token_cache_file, resolve_graph_access_token
from .microsoft_runtime_settings import clear_settings_file
from .session_context import get_session_store
from .types import AgentConfig


@dataclass
class StartupCheckResult:
    """Wynik walidacji — `blocking` musi być puste, żeby uznać konfigurację za gotową do pracy z LLM."""

    ok: bool
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ok_notes: list[str] = field(default_factory=list)


def run_startup_checks(config: AgentConfig) -> StartupCheckResult:
    blocking: list[str] = []
    warnings: list[str] = []
    ok_notes: list[str] = []

    if not config.openrouter_api_key.strip():
        blocking.append(
            "OPENROUTER_API_KEY — brak w .env. Weź klucz z https://openrouter.ai/keys , dopisz do pliku .env obok repo, restart bota."
        )

    if not config.mcp_api_key.strip():
        warnings.append(
            "MCP_API_KEY puste — odpowiedzi bez narzędzi MCP (tylko tryb prosty). Dopisz klucz MCP i zrestartuj, jeśli chcesz pliki / shell / Microsoft przez MCP."
        )
    else:
        try:
            mcp_health(config)
            ok_notes.append(f"MCP ({config.mcp_server_url}): /health OK")
        except Exception as exc:
            warnings.append(
                f"MCP nie odpowiada lub odrzuca klucz: {type(exc).__name__}: {str(exc)[:220]}. "
                "Sprawdź MCP_SERVER_URL, MCP_API_KEY, sieć."
            )

    tok = resolve_graph_access_token(config)
    if tok:
        ok_notes.append("Microsoft Graph: token dostępny (env / plik runtime / cache MSAL).")
    elif not config.microsoft_client_id.strip():
        warnings.append(
            "Microsoft Graph: brak tokenu i brak Client ID (MICROSOFT_CLIENT_ID lub /microsoft-set-client). "
            "Narzędzia microsoft_* w MCP nie zadziałają do czasu logowania."
        )
    else:
        ok_notes.append("Microsoft Graph: Client ID jest — użyj /microsoft-login (device code) albo /microsoft-set-graph-token.")

    if not config.telegram_allowed_chat_ids:
        warnings.append(
            "TELEGRAM_ALLOWED_CHAT_IDS puste — każdy znający link do bota może pisać; dla produkcji ustaw listę chat_id."
        )

    return StartupCheckResult(ok=len(blocking) == 0, blocking=blocking, warnings=warnings, ok_notes=ok_notes)


def format_startup_report_plain(result: StartupCheckResult, *, title: str = "Konfiguracja jarvis1net") -> str:
    """Tekst do Telegrama / logów (bez HTML)."""
    lines = [title, ""]
    if result.ok:
        lines.append("Stan: OK — działamy dalej.")
        for n in result.ok_notes:
            lines.append(f"- {n}")
    else:
        lines.append("Stan: DO UZUPEŁNIENIA — napraw poniższe, potem zrestartuj bota (lub edytuj .env i /restart):")
        for b in result.blocking:
            lines.append(f"- {b}")
    for w in result.warnings:
        lines.append(f"- (uwaga) {w}")
    lines.append("")
    lines.append(
        "Sekrety najlepiej wpisz w .env na serwerze (SSH), nie na publicznym czacie. "
        "Microsoft (bez sekretu aplikacji): /microsoft-set-client … + /microsoft-login."
    )
    return "\n".join(lines)


def reset_runtime_agent_state(config: AgentConfig) -> list[str]:
    """
    Czyści runtime zapisany z czatu + cache MSAL + pamięć rozmów. Nie usuwa .env.
    Pełny „factory reset” agenta w sensie danych lokalnych obok logów.
    """
    out: list[str] = []
    out.append(clear_settings_file(config.audit_log_path))
    out.append(clear_token_cache_file(config))
    store = get_session_store(config.session_context_path)
    store.clear_all_sessions()
    store.save()
    out.append("Wyczyszczono pamięć rozmów (wszystkie sesje w session_paths.json).")
    return out
