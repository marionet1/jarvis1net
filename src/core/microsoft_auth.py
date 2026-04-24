"""Microsoft Graph delegated auth on the agent (device code flow + token cache)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import msal

from .types import AgentConfig


def _authority(tenant_id: str) -> str:
    tid = (tenant_id or "common").strip() or "common"
    return f"https://login.microsoftonline.com/{tid}"


def _scopes(config: AgentConfig) -> list[str]:
    return list(config.microsoft_graph_scopes)


# Device code + silent flows reject these in the scopes argument (MSAL / STS reserved).
_MSAL_SCOPE_BLOCKLIST = frozenset(s.casefold() for s in ("offline_access", "openid", "profile"))


def _msal_request_scopes(config: AgentConfig) -> list[str]:
    out = [s.strip() for s in _scopes(config) if s.strip() and s.strip().casefold() not in _MSAL_SCOPE_BLOCKLIST]
    if not out:
        raise RuntimeError(
            "Brak scope Graph po odfiltrowaniu zarezerwowanych (offline_access/openid/profile). "
            "Ustaw np. User.Read Mail.Read w /microsoft-set-scopes lub MICROSOFT_GRAPH_SCOPES."
        )
    return out


def _cache_path(config: AgentConfig) -> Path:
    return Path(config.microsoft_token_cache_path).expanduser().resolve()


def _public_app(config: AgentConfig) -> tuple[msal.PublicClientApplication, msal.SerializableTokenCache]:
    cid = config.microsoft_client_id.strip()
    if not cid:
        raise RuntimeError("MICROSOFT_CLIENT_ID is not set.")
    cache = msal.SerializableTokenCache()
    path = _cache_path(config)
    if path.exists():
        try:
            cache.deserialize(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    app = msal.PublicClientApplication(
        cid,
        authority=_authority(config.microsoft_tenant_id),
        token_cache=cache,
    )
    return app, cache


def _persist_cache(cache: msal.SerializableTokenCache, config: AgentConfig) -> None:
    if not cache.has_state_changed:
        return
    path = _cache_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cache.serialize(), encoding="utf-8")


def get_graph_access_token_silent(config: AgentConfig) -> str | None:
    """Returns a valid access token from MSAL cache, or None."""
    if not config.microsoft_client_id.strip():
        return None
    app, cache = _public_app(config)
    accounts = app.get_accounts()
    if not accounts:
        _persist_cache(cache, config)
        return None
    result = app.acquire_token_silent(_msal_request_scopes(config), account=accounts[0])
    _persist_cache(cache, config)
    if result and "access_token" in result:
        return str(result["access_token"])
    return None


def resolve_graph_access_token(config: AgentConfig) -> str | None:
    """Static env token wins; otherwise MSAL silent from disk cache."""
    static = config.microsoft_graph_access_token.strip()
    if static:
        if static.lower().startswith("bearer "):
            return static.split(" ", 1)[1].strip() or None
        return static
    return get_graph_access_token_silent(config)


def run_device_code_login(config: AgentConfig, notify: Callable[[str], None]) -> str:
    """
    Runs full device code flow: notify() gets Microsoft's instruction line (URL + code), then blocks until done.
    Returns a short Polish summary for the user.
    """
    app, cache = _public_app(config)
    flow = app.initiate_device_flow(scopes=_msal_request_scopes(config))
    if "user_code" not in flow:
        err = flow.get("error_description") or flow.get("error") or json.dumps(flow)
        raise RuntimeError(f"Nie udało się uruchomić logowania: {err}")
    notify(str(flow.get("message") or "Otwórz stronę Microsoft i wpisz kod urządzenia."))
    result = app.acquire_token_by_device_flow(flow)
    _persist_cache(cache, config)
    if not result or "access_token" not in result:
        detail = (result or {}).get("error_description") or (result or {}).get("error") or "brak access_token"
        raise RuntimeError(str(detail))
    claims = result.get("id_token_claims") or {}
    username = claims.get("preferred_username") or claims.get("email")
    who = f" ({username})" if username else ""
    return f"Połączono z Microsoft Graph{who}. Możesz np. prosić o listę maili z inboxu."


def clear_token_cache_file(config: AgentConfig) -> str:
    """Removes local MSAL cache (logout for this agent)."""
    path = _cache_path(config)
    try:
        if path.exists():
            path.unlink()
            return "Usunięto zapisane tokeny Microsoft (wylogowano z tej instancji agenta)."
        return "Brak pliku cache — nic do usunięcia."
    except OSError as exc:
        return f"Nie udało się usunąć cache: {exc}"
