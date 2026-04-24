# jarvis1net v0.1

`jarvis1net` is a lightweight personal AI assistant powered by OpenRouter.
This release is a simple MVP focused on reliability and clean self-hosted setup.

## What it does

- Conversational replies through OpenRouter (**CLI** `src/main.py` and **Telegram** `src/telegram_bot.py`).
- Optional **MCP tools** over HTTP when `MCP_API_KEY` is set. The agent sends a **Microsoft Graph** bearer token to MCP as **`X-Graph-Authorization`** when available, so each deployment can use a different Microsoft user.
- **Short session memory** (per CLI session / per Telegram chat), persisted as JSON next to the audit log.
- **Startup configuration check**: on Telegram start and in CLI at launch, a compact **OK / incomplete** report covers OpenRouter key, MCP reachability, and Graph token state.
- **Runtime secrets** (optional): `jarvis_runtime_secrets.json` next to the audit log can hold `openrouter_api_key` and/or `mcp_api_key` written from chat (**`/jarvis-set-openrouter-key`**, **`/jarvis-set-mcp-key`**) or CLI; `load_config()` merges this file **over** `.env` for those keys so you can fix keys without editing `.env` (restart not required for key changes).
- **`DISPLAY_TIMEZONE`**: IANA zone (e.g. `Europe/Warsaw`) is injected into the system prompt so the model can quote Graph mail/calendar times in local time.
- Optional **token + cost footer** on replies when `OPENROUTER_SHOW_COST_ESTIMATE=1` (pricing from OpenRouter `/api/v1/models`, cached ~1 h).
- Basic **audit** events to a JSONL log.

## Requirements

- Python 3.12+
- OpenRouter API key (`OPENROUTER_API_KEY` in `.env` **or** saved via `/jarvis-set-openrouter-key`)

## Quick start

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

## Telegram mode

```bash
python src/telegram_bot.py
```

After start, if `TELEGRAM_NOTIFY_ON_START=1`, allowed chats get the configurable startup line plus the same **configuration report** as printed on stdout.

## Useful commands (Telegram and most on CLI)

| Command | Purpose |
|--------|---------|
| `/start`, `/help` | Short help |
| `/info` | HTML: all commands + MCP tool list |
| `/jarvis-config-check` | Re-run startup checks (MCP + Graph + OpenRouter) |
| `/jarvis-set-openrouter-key …` | Save OpenRouter key to `jarvis_runtime_secrets.json` |
| `/jarvis-set-mcp-key …` | Save MCP API key to `jarvis_runtime_secrets.json` |
| `/jarvis-config-reset` | Clear MS runtime file, runtime secrets file, MSAL cache, **all** session memory (same allowlist guard as `/restart`) |
| `/restart` | Restart `jarvis1net-telegram` via user `systemctl` (only if `TELEGRAM_ALLOWED_CHAT_IDS` is set and matches); natural phrases **`restart bot`**, **`restart the bot`**, or legacy Polish phrases also work |
| `/jarvis-limits` | Show MCP round limits, JSON caps, timeouts, `DISPLAY_TIMEZONE`, cost footer flag |
| `/microsoft-set-client`, `/microsoft-login`, … | Microsoft device flow and settings (see below) |

**Security:** if `TELEGRAM_ALLOWED_CHAT_IDS` is empty, **anyone** with the bot link can chat and use the key-saving commands (bootstrap only). For production, set an allowlist.

## MCP (hosted service)

MCP is a **hosted HTTP service**. The agent uses `MCP_SERVER_URL` and `MCP_API_KEY`.

When a Graph token is present, the tool **`microsoft_integration_status`** is **removed** from the manifest sent to the model (smaller payloads; use real `microsoft_*` tools instead).

## Environment variables

See `.env.example`. Highlights:

- `OPENROUTER_API_KEY`, `MODEL`, `OPENROUTER_SHOW_COST_ESTIMATE`
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_IDS`, `TELEGRAM_POLLING_TIMEOUT_SEC`, `TELEGRAM_NOTIFY_ON_START`, `TELEGRAM_CLEAR_SESSION_ON_START`, optional `TELEGRAM_STARTUP_MESSAGE`
- `AUDIT_LOG_PATH`, `SESSION_CONTEXT_PATH` (optional)
- `MCP_SERVER_URL`, `MCP_API_KEY`, `MCP_TIMEOUT_SEC`, `MCP_MAX_TOOL_ROUNDS`, result size caps, `MCP_CHAT_COMPLETION_MAX_TOKENS`
- **`DISPLAY_TIMEZONE`** — IANA timezone for Graph time quoting in answers
- **Microsoft Graph** (optional): agent sends `X-Graph-Authorization` to MCP; no Azure app secret on the agent for device flow.
  - **Device code (Telegram/CLI):** set `MICROSOFT_CLIENT_ID` in `.env`, **or** **`/microsoft-set-client <Azure-Client-ID> [tenant]`** (saved to `microsoft_agent_settings.json` next to logs — no restart). Then **`/microsoft-login`** delivers link + code; tokens go to `MICROSOFT_TOKEN_CACHE_PATH` or next to the audit log. **`/microsoft-logout`** clears cache and pasted runtime token. **`/microsoft-clear-runtime`** removes chat-saved Client ID/scopes/token file fields. **`/microsoft-show-settings`** shows effective Client ID, tenant, scopes, redirect URIs, token source.
  - **Static token:** `MICROSOFT_GRAPH_ACCESS_TOKEN` in `.env` overrides cache for quick tests.
  - **Default delegated scopes:** `User.Read Mail.ReadWrite Mail.Send Calendars.ReadWrite Files.ReadWrite.All` — after changing permissions in Azure, run **`/microsoft-logout`** then **`/microsoft-login`** to refresh consent.

## Microsoft Azure checklist (device code)

1. **Account type** in the app registration must match what you use. **Default:** agent and `/microsoft-set-client <UUID>` (no second argument) use **`organizations`** — fewer device-flow issues than `common`. Personal-only: `/microsoft-set-client <UUID> consumers` and redirect `.../consumers/...`.
2. **Authentication → Allow public client flows:** **Yes**.
3. **Platform “Mobile and desktop applications”:** register **exactly one** redirect URI matching `MICROSOFT_TENANT_ID` (same as `/microsoft-show-settings`): `https://login.microsoftonline.com/<tenant>/oauth2/nativeclient` where `<tenant>` is `common`, `organizations`, `consumers`, or directory **GUID**. **Do not** register multiple segments at once (`common` + `organizations` + `consumers`) — with `authority=common` Microsoft may redirect to another host with an **incomplete** request and you see **`response_type`** / 404. Work account: agent `organizations` and Azure **only** `.../organizations/oauth2/nativeclient`; personal: `consumers` + only `.../consumers/...`; mixed: `common` + only `.../common/...`.
4. **Remove** old **Web** redirect to `https://mcp.jarvis1.net/.../oauth/callback` — unused and confuses the flow.
5. **API permissions (Delegated):** at minimum `User.Read`, `Mail.ReadWrite`, `Mail.Send`, `Calendars.ReadWrite`, `Files.ReadWrite.All` — **Grant admin consent** when your tenant requires it.
6. **Manifest (optional):** `allowPublicClient` = **true** if the UI toggle does not stick.
7. After Azure changes: **`/microsoft-logout`** → **`/microsoft-login`**. Microsoft “phishing” warnings on URLs with `error=` are often false positives (query error, not real phishing).
8. **Brave / blockers:** temporarily allow `microsoft.com` and `login.microsoftonline.com` (or use Chrome/Edge) — they can break OAuth redirects.
9. If **`invalid_request`** on `nativeclient` persists: set tenant to directory **GUID** in Azure and the same GUID in the agent as `MICROSOFT_TENANT_ID` and in the redirect `.../<GUID>/oauth2/nativeclient`.

## Security notes

- Keep `.env` private; add `jarvis_runtime_secrets.json` and `microsoft_agent_settings.json` to backups policy — they contain secrets when used.
- Never commit real API keys or bot tokens (`.gitignore` includes `jarvis_runtime_secrets.json`).
- Share only `.env.example` in public repositories.

## Wishlist

Ideas worth adding later (no fixed order or deadlines).

- Longer and smarter conversation memory policies.
- Session commands: `/new`, `/reset` (explicit user controls).
- Better logging and debugging (`audit`, errors, metrics).
- ~~A dedicated `mcp-jarvis1net` server.~~ (done in separate repository)
- Tool registry (name, description, input schema).
- Optional: OAuth redirect / PKCE in a small web callback instead of device code only; per-Telegram-chat Microsoft accounts.
- Approval gate before high-risk actions.
- Basic user roles and permissions.
- Document import into knowledge storage (RAG in a later stage).
- Simple status panel (`healthcheck`, `uptime`, `last error`).
- Smoke tests for CLI and Telegram.
- `docker-compose` for fast deployment.
- Better VPS deployment guide.
- Change tracking (`CHANGELOG` / release notes).

## Project docs

- `RELEASE_NOTES_v0.1.md`
