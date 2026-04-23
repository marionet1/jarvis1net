# jarvis1net v0.1

`jarvis1net` is a lightweight personal AI assistant powered by OpenRouter.
This release is a simple MVP focused on reliability and clean self-hosted setup.

## What It Does

- Provides conversational responses through OpenRouter (CLI + Telegram).
- Optional **MCP filesystem tools** over HTTP when `MCP_API_KEY` is set (private hosted MCP service).
- Short **session chat history** (same Telegram chat / CLI session), persisted to a small JSON file next to audit logs.
- Writes basic audit events to a JSONL log file.

## Requirements

- Python 3.12+
- OpenRouter API key (`OPENROUTER_API_KEY`)

## Quick Start

### Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -c "import json, subprocess, sys; deps=json.load(open('requirements.json', encoding='utf-8'))['python_dependencies']; subprocess.check_call([sys.executable, '-m', 'pip', 'install', *deps])"
cp .env.example .env
python src/main.py
```

### Windows (PowerShell)

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
py -c "import json, subprocess, sys; deps=json.load(open('requirements.json', encoding='utf-8'))['python_dependencies']; subprocess.check_call([sys.executable, '-m', 'pip', 'install', *deps])"
Copy-Item .env.example .env
py src/main.py
```

## Telegram Mode

```bash
python src/telegram_bot.py
```

## MCP (hosted service)

MCP is provided as a **hosted service**. This agent connects only through `MCP_SERVER_URL` and an `MCP_API_KEY` issued by the operator.
To request an API key, send a DM on GitHub: [github.com/marionet1](https://github.com/marionet1).

## Environment Variables

- `OPENROUTER_API_KEY`, `MODEL`
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_IDS`, `TELEGRAM_POLLING_TIMEOUT_SEC`
- `AUDIT_LOG_PATH`, `SESSION_CONTEXT_PATH` (optional)
- `MCP_SERVER_URL`, `MCP_API_KEY`, `MCP_TIMEOUT_SEC`, `MCP_MAX_TOOL_ROUNDS`

## Security Notes

- Keep `.env` private.
- Never commit real API keys or bot tokens.
- Share only `.env.example` in public repositories.

## Project Docs

- `RELEASE_NOTES_v0.1.md`
- `WISHLIST.md`
