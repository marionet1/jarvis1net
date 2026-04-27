# jarvis1net (agent-jarvis1net)

Python AI assistant runtime for OpenRouter with Telegram and CLI entry points.
It connects to `mcp-jarvis1net` through stdio.

Related repositories:
- [stack-jarvis1net](https://github.com/marionet1/stack-jarvis1net)
- [mcp-jarvis1net](https://github.com/marionet1/mcp-jarvis1net)

## What this agent provides

- Chat responses via OpenRouter.
- Telegram bot mode (`src/channels/telegram.py`) and CLI mode (`src/main.py`).
- MCP tool calls delegated to `mcp-jarvis1net` over stdio.
- Runtime session context and JSONL audit logging.
- Optional Microsoft Graph integration (MSAL device code flow).

## Requirements

- Python 3.12+
- OpenRouter API key (`OPENROUTER_API_KEY` or `/jarvis-set-openrouter-key`)
- A reachable MCP stdio server command configured in `config/runtime_config.json`

## Recommended run mode (Docker stack)

From the parent `stack-jarvis1net` repository:

```bash
cp agent-jarvis1net/.env.example .env
docker compose build
docker compose up -d
```

The compose service name is `jarvis1net`.

Run CLI inside the same image:

```bash
docker compose run --rm jarvis1net python3 src/main.py
```

## Local run (agent only)

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -c "import json,subprocess,sys; d=json.load(open('requirements.json', encoding='utf-8')); subprocess.check_call([sys.executable,'-m','pip','install',*d['python_dependencies']])"
cp .env.example .env
python3 src/main.py
```

If running this repository outside the Docker stack, make sure `config/runtime_config.json` points to the MCP server:

```json
{
  "mcp_stdio_command": "python3",
  "mcp_stdio_args": ["/absolute/path/to/mcp-jarvis1net/src/server.py"]
}
```

## Telegram commands

- `/start`, `/help`
- `/info`
- `/jarvis-config-check`
- `/jarvis-set-openrouter-key ...`
- `/jarvis-config-reset`
- `/restart`
- `/jarvis-limits`
- `/microsoft-*`

For production, set `telegram_allowed_chat_ids` in `config/runtime_config.json`.

## Configuration

- Secrets in `.env` (see `.env.example`):
  - `OPENROUTER_API_KEY`
  - `TELEGRAM_BOT_TOKEN`
  - optional `MCP_GRAPH_ACCESS_TOKEN`
- Non-secret runtime settings in `config/runtime_config.json`:
  - model, Telegram behavior, MCP stdio command/args, timeouts, paths, Microsoft tenant/scopes, timezone.

## Project layout

- `config/runtime_config.json` - single place for non-secret runtime config
- `src/core/` - agent runtime (LLM loop, typed config model, session/audit)
- `src/core/runtime_config.py` - runtime config loader + startup checks/reset helpers
- `src/integrations/mcp/` - MCP stdio client + tool bridge integration
- `src/integrations/microsoft/` - Microsoft auth/cache integration
- `src/integrations/openrouter/` - OpenRouter client + pricing/cost helpers
- `src/channels/` - channel-facing modules (CLI, Telegram helpers)
- `src/main.py` - CLI entry point
- `src/channels/telegram.py` - Telegram entry point
- `deploy/` - deployment helper scripts

## License

See the parent stack repository policy and repository license files.
