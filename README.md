# jarvis1net v0.1

`jarvis1net` is a lightweight personal AI assistant powered by OpenRouter.
This release is a simple MVP focused on reliability and clean self-hosted setup.

## What It Does

- Provides conversational responses through OpenRouter (CLI + Telegram).
- Optional **MCP tools** over HTTP when `MCP_API_KEY` is set (private hosted MCP service), including **`microsoft_*`** when you set **`MICROSOFT_GRAPH_ACCESS_TOKEN`** (the agent sends it to MCP as `X-Graph-Authorization`, so each deployment can use a different Microsoft user).
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
- **Microsoft Graph** (optional): MCP still has no Azure secrets; the agent sends `X-Graph-Authorization`.
  - **Device code (Telegram):** either set `MICROSOFT_CLIENT_ID` in `.env`, **or** send **`/microsoft-set-client <Azure-Client-ID> [tenant]`** in chat (saved to `microsoft_agent_settings.json` next to audit logs — no restart). Then **`/microsoft-login`** sends the link + code; tokens go to `MICROSOFT_TOKEN_CACHE_PATH` or next to the audit log. **`/microsoft-logout`** clears the token cache; **`/microsoft-clear-runtime`** removes chat-saved Client ID/scopes. **`/microsoft-show-settings`** summarizes effective config.
  - **Static token:** `MICROSOFT_GRAPH_ACCESS_TOKEN` overrides the cache if set (e.g. short tests).
  - **Domyślne scope (token):** `User.Read Mail.ReadWrite Mail.Send Calendars.ReadWrite Files.ReadWrite.All` — po zmianie w Azure wyślij `/microsoft-logout` potem `/microsoft-login`, żeby odświeżyć zgodę.

## Microsoft Azure — checklista (device code)

1. **Typ konta** w rejestracji: zgodny z tym, czego używasz (np. tylko organizacja vs konta osobiste). Dla samych kont służbowych często lepiej **`MICROSOFT_TENANT_ID=organizations`** (w czacie: `/microsoft-set-client <UUID> organizations`) zamiast `common`.
2. **Authentication → Allow public client flows:** **Yes**.
3. **Platform „Mobile and desktop applications”:** dodaj redirect **dokładnie** taki, jaki pokazuje `/microsoft-show-settings` (np. `https://login.microsoftonline.com/common/oauth2/nativeclient` albo `.../organizations/...` albo `.../<GUID-tenanta>/...`). **Segment w URL musi być taki sam jak `MICROSOFT_TENANT_ID`.** Możesz dodać **dwa** wpisy (np. `common` i `organizations`), jeśli eksperymentujesz.
4. **Usuń** stary redirect **Web** na `https://mcp.jarvis1.net/.../oauth/callback` — nie jest używany i myli przepływ.
5. **API permissions (Delegated):** minimum `User.Read`, `Mail.ReadWrite`, `Mail.Send`, `Calendars.ReadWrite`, `Files.ReadWrite.All` — **Grant admin consent** dla katalogu (jeśli masz uprawnienia).
6. **Manifest (opcjonalnie):** `allowPublicClient` = **true** (jeśli przełącznik w UI nie zadziała).
7. Po zmianach w Azure: **`/microsoft-logout`** → **`/microsoft-login`**. Ostrzeżenie Microsoftu o „phishingu” przy URL z `error=` bywa fałszywe — chodzi o błąd w query, nie o realny phishing.
8. Jeśli nadal **`invalid_request`** na `nativeclient`: ustaw tenant na **GUID katalogu** (Directory tenant ID) w Azure i ten sam GUID w agencie jako `MICROSOFT_TENANT_ID` oraz w redirect `.../<GUID>/oauth2/nativeclient`.

## Security Notes

- Keep `.env` private.
- Never commit real API keys or bot tokens.
- Share only `.env.example` in public repositories.

## Project Docs

- `RELEASE_NOTES_v0.1.md`
- `WISHLIST.md`
