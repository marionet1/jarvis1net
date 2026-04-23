# Release Notes v0.1

## Overview

`jarvis1net` v0.1 is the first public MVP release of a lightweight personal AI agent.

## Highlights

- Chat-only architecture for reliability and easy setup.
- Single-model flow configured via `MODEL`.
- CLI interface (`src/main.py`) and Telegram interface (`src/telegram_bot.py`).
- Audit logging to JSONL (`AUDIT_LOG_PATH`) for basic traceability.
- Short session memory (per CLI session and per Telegram chat).
- Optional MCP HTTP integration (filesystem tools) via `MCP_SERVER_URL` + `MCP_API_KEY`.

## Scope

- MCP is optional and only used when API key configuration is present.
- Filesystem tool execution is available through the hosted MCP service.
- Session memory is short-term only (not long-term knowledge storage).

## Security

- Secrets are loaded from `.env`.
- Public repository should include only `.env.example`.

## Notes

Version remains **0.1** and focuses on a stable MVP baseline (CLI + Telegram + optional hosted MCP).
