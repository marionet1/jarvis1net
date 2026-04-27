from __future__ import annotations

from pathlib import Path

from integrations.microsoft import read_settings, settings_path, validate_client_id
from core.types import AgentConfig


def parse_microsoft_set_client(command_text: str) -> tuple[bool, str, dict[str, object] | None]:
    parts = command_text.strip().split()
    if len(parts) < 2:
        return False, "Usage: /microsoft-set-client <Client-ID> [tenant]", None
    client_id = parts[1].strip()
    tenant = parts[2].strip() if len(parts) > 2 else "consumers"
    if not validate_client_id(client_id):
        return False, "Client ID must be a full UUID from Azure.", None
    return True, "", {"client_id": client_id, "tenant_id": tenant}


def parse_microsoft_set_tenant(command_text: str) -> tuple[bool, str, str | None]:
    parts = command_text.strip().split()
    if len(parts) < 2:
        return False, "Usage: /microsoft-set-tenant <consumers|organizations|common|GUID>", None
    raw = parts[1].strip()
    t = raw.casefold()
    ok = t in ("common", "organizations", "consumers") or validate_client_id(raw)
    if not ok:
        return False, "Unknown tenant.", None
    return True, "", raw


def parse_microsoft_set_scopes(command_text: str) -> tuple[bool, str, list[str] | None]:
    parts = command_text.strip().split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        return False, "", None
    scope_list = [s.strip() for s in parts[1].replace(",", " ").split() if s.strip()]
    if not scope_list:
        return False, "Provide at least one scope.", None
    return True, "", scope_list


def parse_microsoft_set_graph_token(command_text: str) -> tuple[bool, str, str | None]:
    parts = command_text.strip().split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        return False, "Usage: /microsoft-set-graph-token <access_token>", None
    token = parts[1].strip()
    if token.casefold().startswith("bearer "):
        token = token[7:].strip()
    if len(token) < 30:
        return False, "Token too short.", None
    return True, "", token


def build_microsoft_settings_lines(config: AgentConfig) -> list[str]:
    rt = read_settings(config.audit_log_path)
    src = "file" if rt.get("client_id") else "config"
    has_cache = Path(config.microsoft_token_cache_path).expanduser().exists()
    ten_rt = str(rt.get("tenant_id") or "").strip()
    if ten_rt:
        ten_src = "file (overrides config)"
    else:
        ten_src = "config default"
    # Access token is secret and may still be provided by environment.
    import os
    tok_env = bool(os.getenv("MCP_GRAPH_ACCESS_TOKEN", "").strip())
    tok_rt = bool(isinstance(rt.get("graph_access_token"), str) and str(rt.get("graph_access_token")).strip())
    if tok_env:
        tok_src = "env MCP_GRAPH_ACCESS_TOKEN"
    elif tok_rt:
        tok_src = "file graph_access_token"
    else:
        tok_src = "MSAL po /microsoft-login"
    return [
        f"Client ID: {config.microsoft_client_id or '(none)'} (source: {src})",
        f"Tenant: {config.microsoft_tenant_id} (source: {ten_src})",
        f"Scopes: {' '.join(config.microsoft_graph_scopes)}",
        f"Graph token: {tok_src}",
        f"Settings file: {settings_path(config.audit_log_path)}",
        f"MSAL token cache: {'yes' if has_cache else 'no'}",
    ]
