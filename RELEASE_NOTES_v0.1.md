# Release Notes v0.1

## Overview

`jarvis1net` v0.1 is the first public MVP release of a lightweight personal AI agent.

## Highlights

- Chat-focused architecture for reliability and easy setup.
- Single-model flow configured via `MODEL`.
- CLI (`src/main.py`) and Telegram (`src/telegram_bot.py`).
- Audit logging to JSONL (`AUDIT_LOG_PATH`).
- Short session memory (per CLI session and per Telegram chat), file next to audit logs.
- Optional MCP HTTP integration via `MCP_SERVER_URL` + `MCP_API_KEY` (filesystem, shell diagnostics, Microsoft Graph tools when a token is supplied).
- **Startup configuration check** (OpenRouter key, MCP health, Graph token hint) printed on CLI/Telegram start; **`/jarvis-config-check`** repeats it.
- **`jarvis_runtime_secrets.json`**: optional overlay for `OPENROUTER_API_KEY` / `MCP_API_KEY` from **`/jarvis-set-openrouter-key`** and **`/jarvis-set-mcp-key`** (or CLI); merged in `load_config()` over `.env`.
- **`/jarvis-config-reset`**: clears Microsoft runtime settings file, `jarvis_runtime_secrets.json`, MSAL token cache, and all session memory (does not edit `.env` on disk).
- **`DISPLAY_TIMEZONE`** for local-time quoting of Graph data in model replies.
- **`OPENROUTER_SHOW_COST_ESTIMATE`**: optional USD estimate in reply footer from OpenRouter public pricing API.
- MCP manifest filtering: when a Graph token is present, **`microsoft_integration_status`** is omitted from the tool list sent to the model.

## Scope

- MCP is optional and only used when an API key is configured.
- Session memory is short-term only (not long-term RAG).

## Security

- Secrets load from `.env` and optional runtime JSON files next to logs.
- Public repository should ship only `.env.example`.

## Notes

Version **0.1** targets a stable MVP baseline (CLI + Telegram + optional hosted MCP + Microsoft Graph via agent-held token).
