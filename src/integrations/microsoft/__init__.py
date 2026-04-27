"""Microsoft integration for agent-side auth/runtime token handling."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import msal

from core.types import AgentConfig

_SETTINGS_NAME = "microsoft_agent_settings.json"

_CLIENT_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
)


def settings_path(audit_log_path: str) -> Path:
    return Path(audit_log_path).expanduser().resolve().parent / _SETTINGS_NAME


def read_settings(audit_log_path: str) -> dict[str, Any]:
    path = settings_path(audit_log_path)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_merged_settings(audit_log_path: str, patch: dict[str, Any]) -> None:
    cur = read_settings(audit_log_path)
    for key, val in patch.items():
        if val is None:
            cur.pop(key, None)
        else:
            cur[key] = val
    path = settings_path(audit_log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cur, indent=2, sort_keys=True), encoding="utf-8")


def clear_settings_file(audit_log_path: str) -> str:
    path = settings_path(audit_log_path)
    try:
        if path.exists():
            path.unlink()
            return "Removed Microsoft settings file from chat (microsoft_agent_settings.json)."
        return "No saved settings file — nothing to remove."
    except OSError as exc:
        return f"Could not remove settings file: {exc}"


def validate_client_id(value: str) -> bool:
    return bool(_CLIENT_ID_RE.match(value.strip()))


def _authority(tenant_id: str) -> str:
    tid = (tenant_id or "consumers").strip() or "consumers"
    return f"https://login.microsoftonline.com/{tid}"


def recommended_native_redirect_uri(tenant_id: str) -> str:
    tid = (tenant_id or "consumers").strip() or "consumers"
    return f"https://login.microsoftonline.com/{tid}/oauth2/nativeclient"


def recommended_native_redirect_uris(tenant_id: str) -> list[str]:
    return [recommended_native_redirect_uri(tenant_id)]


def _scopes(config: AgentConfig) -> list[str]:
    return list(config.microsoft_graph_scopes)


_MSAL_SCOPE_BLOCKLIST = frozenset(s.casefold() for s in ("offline_access", "openid", "profile"))


def _msal_request_scopes(config: AgentConfig) -> list[str]:
    out = [s.strip() for s in _scopes(config) if s.strip() and s.strip().casefold() not in _MSAL_SCOPE_BLOCKLIST]
    if not out:
        raise RuntimeError(
            "No Graph scopes after filtering reserved (offline_access/openid/profile). "
            "Set e.g. User.Read Mail.ReadWrite in /microsoft-set-scopes or runtime_config.json."
        )
    return out


def _cache_path(config: AgentConfig) -> Path:
    return Path(config.microsoft_token_cache_path).expanduser().resolve()


def _public_app(config: AgentConfig) -> tuple[msal.PublicClientApplication, msal.SerializableTokenCache]:
    cid = config.microsoft_client_id.strip()
    if not cid:
        raise RuntimeError("microsoft_client_id is not set in runtime config.")
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
    static = config.microsoft_graph_access_token.strip()
    if static:
        if static.lower().startswith("bearer "):
            return static.split(" ", 1)[1].strip() or None
        return static
    return get_graph_access_token_silent(config)


def run_device_code_login(config: AgentConfig, notify: Callable[[str], None]) -> str:
    app, cache = _public_app(config)
    flow = app.initiate_device_flow(scopes=_msal_request_scopes(config))
    if "user_code" not in flow:
        err = flow.get("error_description") or flow.get("error") or json.dumps(flow)
        raise RuntimeError(f"Could not start device login: {err}")
    notify(str(flow.get("message") or "Open the Microsoft page and enter the device code."))
    vu = flow.get("verification_uri")
    if isinstance(vu, str) and vu.strip():
        notify("Device login page (Microsoft):\n" + vu.strip())
    vuc = flow.get("verification_uri_complete")
    if isinstance(vuc, str) and vuc.strip():
        notify("One-click link (if your browser supports it):\n" + vuc.strip())
    result = app.acquire_token_by_device_flow(flow)
    _persist_cache(cache, config)
    if not result or "access_token" not in result:
        r = result or {}
        detail = r.get("error_description") or r.get("error") or "no access_token"
        redir = recommended_native_redirect_uri(config.microsoft_tenant_id)
        hint = (
            " Tenant/redirect mismatch: work account needs organizations + …/organizations/oauth2/nativeclient; "
            "personal MSA needs consumers + …/consumers/oauth2/nativeclient. "
            f"Current redirect for your tenant in the agent: {redir!r}. "
            "Or: /microsoft-set-graph-token + az account get-access-token --resource https://graph.microsoft.com -o tsv."
        )
        raise RuntimeError(str(detail) + hint)
    claims = result.get("id_token_claims") or {}
    username = claims.get("preferred_username") or claims.get("email")
    who = f" ({username})" if username else ""
    return f"Connected to Microsoft Graph{who}. You can e.g. ask for mail from the inbox."


def clear_token_cache_file(config: AgentConfig) -> str:
    path = _cache_path(config)
    try:
        if path.exists():
            path.unlink()
            return "Removed saved Microsoft tokens (logged out for this agent instance)."
        return "No cache file — nothing to remove."
    except OSError as exc:
        return f"Could not remove cache: {exc}"
