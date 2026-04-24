"""OpenRouter key saved from chat (next to logs); override .env when present in this file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_FILE = "jarvis_runtime_secrets.json"


def jarvis_runtime_path(audit_log_path: str) -> Path:
    return Path(audit_log_path).expanduser().resolve().parent / _FILE


def read_jarvis_runtime(audit_log_path: str) -> dict[str, Any]:
    path = jarvis_runtime_path(audit_log_path)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_merged_jarvis_runtime(audit_log_path: str, patch: dict[str, Any]) -> None:
    cur = read_jarvis_runtime(audit_log_path)
    for key, val in patch.items():
        if val is None:
            cur.pop(key, None)
        else:
            cur[key] = val
    path = jarvis_runtime_path(audit_log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cur, indent=2, sort_keys=True), encoding="utf-8")


def clear_jarvis_runtime_file(audit_log_path: str) -> str:
    path = jarvis_runtime_path(audit_log_path)
    try:
        if path.exists():
            path.unlink()
            return "Removed jarvis_runtime_secrets.json (keys saved from chat)."
        return "No jarvis_runtime_secrets.json — nothing to remove."
    except OSError as exc:
        return f"Could not remove jarvis_runtime_secrets.json: {exc}"
