"""Optional Microsoft settings persisted next to audit logs (set from Telegram / CLI without .env)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_SETTINGS_NAME = "microsoft_agent_settings.json"

# Application (client) ID from Azure is a GUID.
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
