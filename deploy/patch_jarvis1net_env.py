#!/usr/bin/env python3
"""Append missing Microsoft/MCP keys to .env (no overwrites). Run on server: python3 patch_jarvis1net_env.py"""
from __future__ import annotations

import pathlib

ENV_PATH = pathlib.Path("/home/jump/jarvis1net/.env")

DEFAULTS: list[tuple[str, str]] = [
    ("MCP_SERVER_URL", "https://mcp.jarvis1.net"),
    ("AUDIT_LOG_PATH", "/home/jump/jarvis1net/logs/audit.jsonl"),
    ("MICROSOFT_TENANT_ID", "organizations"),
    (
        "MICROSOFT_GRAPH_SCOPES",
        "User.Read Mail.ReadWrite Mail.Send Calendars.ReadWrite Files.ReadWrite.All",
    ),
    ("MICROSOFT_GRAPH_ACCESS_TOKEN", ""),
    ("MICROSOFT_CLIENT_ID", ""),
    ("MICROSOFT_TOKEN_CACHE_PATH", ""),
    ("MCP_TIMEOUT_SEC", "15"),
    ("MCP_MAX_TOOL_ROUNDS", "10"),
    ("MCP_TOOL_RESULT_MAX_CHARS", "12000"),
    ("SESSION_CONTEXT_PATH", ""),
]


def main() -> None:
    text = ENV_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    have: set[str] = set()
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        have.add(s.split("=", 1)[0].strip())

    to_add = [f"{k}={v}" for k, v in DEFAULTS if k not in have]
    if not to_add:
        print("Nothing to add — keys already present.")
        return
    block = "\n# auto-added Microsoft/MCP defaults (jarvis1net deploy)\n" + "\n".join(to_add) + "\n"
    ENV_PATH.write_text(text.rstrip() + block, encoding="utf-8")
    print("Added keys:", ", ".join(k for k, _ in DEFAULTS if k not in have))


if __name__ == "__main__":
    main()
