# Release Notes v0.1

## Overview

`jarvis1net` v0.1 is the first public MVP release of a lightweight personal AI agent.

## Highlights

- Chat-only architecture for reliability and easy setup.
- Single-model flow configured via `MODEL`.
- CLI interface (`src/main.py`) and Telegram interface (`src/telegram_bot.py`).
- Audit logging to JSONL (`AUDIT_LOG_PATH`) for basic traceability.

## Scope

- No MCP integrations.
- No tool execution.
- No long-term conversation memory.

## Security

- Secrets are loaded from `.env`.
- Public repository should include only `.env.example`.

## Notes

This version focuses on simplicity and clean project structure before adding advanced features.
