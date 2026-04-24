# jarvis1net

`jarvis1net` is a lightweight personal AI assistant powered by OpenRouter, designed for a clean self‑hosted stack with optional Telegram and MCP.

**VPS / pełna instrukcja (Docker, struktura katalogów):** zobacz [README w katalogu nadrzędnym (JUMP)](../README.md).

## What it does

- Conversational replies through OpenRouter (**CLI** `src/main.py` and **Telegram** `src/telegram_bot.py`).
- **MCP tools** (`mcp-jarvis1net` jako podproces Node po **stdio**). Dla Microsoft Graph token jest przekazywany w argumencie `graph_access_token` przy narzędziach `microsoft_*`.
- **Short session memory** (per CLI session / per Telegram chat), persisted as JSON next to the audit log.
- **Startup configuration check**: on Telegram start and in CLI at launch, a compact **OK / incomplete** report covers OpenRouter key, MCP reachability, and Graph token state.
- **Runtime secrets** (optional): `jarvis_runtime_secrets.json` can hold `openrouter_api_key` from `/jarvis-set-openrouter-key` (zapis w katalogu danych; w Dockerze `/app/data`).
- **`DISPLAY_TIMEZONE`**: IANA zone (e.g. `Europe/Warsaw`) is injected into the system prompt so the model can quote Graph mail/calendar times in local time.
- Optional **token + cost footer** on replies when `OPENROUTER_SHOW_COST_ESTIMATE=1` (pricing from OpenRouter `/api/v1/models`, cached ~1 h).
- Basic **audit** events to a JSONL log.

## Requirements

- Python 3.12+
- OpenRouter API key (`OPENROUTER_API_KEY` in `.env` **or** saved via `/jarvis-set-openrouter-key`)

## Docker (pełny stos: agent + MCP w jednym obrazie)

Budowanie i uruchomienie odbywa się z **katalogu nadrzędnego** (tam gdzie leżą równolegle `jarvis1net/`, `mcp-jarvis1net/`, główny `Dockerfile`). Zobacz sekcję *Wdrożenie na VPS* w [README nadrzędnego katalogu](../README.md). Skrót:

```bash
cp jarvis1net/.env.example .env
# uzupełnij klucze i tokeny, potem:
docker compose build && docker compose up -d
```

Obraz buduje **mcp-jarvis1net** w `/opt/mcp-jarvis1net` i odpala bota `python3 src/telegram_bot.py`. Dane: wolumen `jarvis_data` → `/app/data`.

CLI zamiast Telegrama: `docker compose run --rm jarvis python3 src/main.py`.

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
| `/jarvis-config-reset` | Clear MS runtime file, runtime secrets file, MSAL cache, **all** session memory (same allowlist guard as `/restart`) |
| `/restart` | Restart `jarvis1net-telegram` via user `systemctl` (only if `TELEGRAM_ALLOWED_CHAT_IDS` is set and matches); natural phrases **`restart bot`**, **`restart the bot`**, or legacy Polish phrases also work |
| `/jarvis-limits` | Show MCP round limits, JSON caps, timeouts, `DISPLAY_TIMEZONE`, cost footer flag |
| `/microsoft-set-client`, `/microsoft-login`, … | Microsoft device flow and settings (see below) |

**Security:** if `TELEGRAM_ALLOWED_CHAT_IDS` is empty, **anyone** with the bot link can chat and use the key-saving commands (bootstrap only). For production, set an allowlist.

## MCP (stdio)

- Ustaw `MCP_STDIO_COMMAND=node` i `MCP_STDIO_ARGS` = JSON `["/ścieżka/do/mcp-jarvis1net/dist/index.js"]` (albo `MCP_STDIO_NODE_SCRIPT`). Proces Node uruchamiany jest w podprocesie; klient MCP w Pythonie łączy się po stdio. W obrazie Docker ścieżka domyślna to `/opt/mcp-jarvis1net/dist/index.js`.

Gdy mamy token Graph, `microsoft_integration_status` jest usuwany z listy (`filter_mcp_tools_when_graph_token_present`).

## Environment variables

See `.env.example`. Highlights:

- `OPENROUTER_API_KEY`, `MODEL`, `OPENROUTER_SHOW_COST_ESTIMATE`
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_IDS`, `TELEGRAM_POLLING_TIMEOUT_SEC`, `TELEGRAM_NOTIFY_ON_START`, `TELEGRAM_CLEAR_SESSION_ON_START`, optional `TELEGRAM_STARTUP_MESSAGE`
- `AUDIT_LOG_PATH`, `SESSION_CONTEXT_PATH` (optional)
- `MCP_STDIO_COMMAND`, `MCP_STDIO_ARGS` (lub `MCP_STDIO_NODE_SCRIPT`), `MCP_ALLOWED_ROOTS`
- `MCP_TIMEOUT_SEC`, `MCP_MAX_TOOL_ROUNDS`, result size caps, `MCP_CHAT_COMPLETION_MAX_TOKENS`
- **`DISPLAY_TIMEZONE`** — IANA timezone for Graph time quoting in answers
- **Microsoft Graph** (optional): token w `graph_access_token` w argumentach narzędzi `microsoft_*` (przekazywany z agenta do MCP). Żadnego client secretu urządzenia w repozytorium.
  - **Device code (Telegram/CLI):** set `MICROSOFT_CLIENT_ID` in `.env`, **or** **`/microsoft-set-client <Azure-Client-ID> [tenant]`** (saved to `microsoft_agent_settings.json` next to logs — no restart). Then **`/microsoft-login`** delivers link + code; tokens go to `MICROSOFT_TOKEN_CACHE_PATH` or next to the audit log. **`/microsoft-logout`** clears cache and pasted runtime token. **`/microsoft-clear-runtime`** removes chat-saved Client ID/scopes/token file fields. **`/microsoft-show-settings`** shows effective Client ID, tenant, scopes, redirect URIs, token source.
  - **Static token:** `MICROSOFT_GRAPH_ACCESS_TOKEN` in `.env` overrides cache for quick tests.
  - **Default delegated scopes:** `User.Read Mail.ReadWrite Mail.Send Calendars.ReadWrite Files.ReadWrite.All` — after changing permissions in Azure, run **`/microsoft-logout`** then **`/microsoft-login`** to refresh consent.

## Microsoft Azure checklist (device code)

1. **Account type** in the app registration must match what you use. **Default:** agent and `/microsoft-set-client <UUID>` (no second argument) use **`consumers`** (personal MSA) + Azure redirect `.../consumers/oauth2/nativeclient`. **Work/school only:** set `MICROSOFT_TENANT_ID=organizations` or `/microsoft-set-client <UUID> organizations` and redirect `.../organizations/...` (for work accounts this is often easier than `common`).
2. **Authentication → Allow public client flows:** **Yes**.
3. **Platform “Mobile and desktop applications”:** register **exactly one** redirect URI matching `MICROSOFT_TENANT_ID` (same as `/microsoft-show-settings`): `https://login.microsoftonline.com/<tenant>/oauth2/nativeclient` where `<tenant>` is `common`, `organizations`, `consumers`, or directory **GUID**. **Do not** register multiple segments at once (`common` + `organizations` + `consumers`) — with `authority=common` Microsoft may redirect to another host with an **incomplete** request and you see **`response_type`** / 404. Work account: agent `organizations` and Azure **only** `.../organizations/oauth2/nativeclient`; personal: `consumers` + only `.../consumers/...`; mixed: `common` + only `.../common/...`.
4. **Usuń** nieużywane **Web** redirect URI w rejestracji aplikacji — tylko **Mobile and desktop** (nativeclient) zgodnie z krokiem 3.
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
- ~~VPS deployment — zobacz README w katalogu nadrzędnym.~~
- Change tracking (`CHANGELOG` / release notes).

### Automation (planned)

Goal: extend **mcp-jarvis1net** (or the agent host) with tools so the assistant can:

1. **Schedule work** — e.g. manage **user-level cron** entries or **systemd user timers** for recurring jobs (with strict allowlists: which commands, which script paths, no root cron).
2. **Author scripts** — e.g. write **Python** (or shell) files under **allowed roots only**, same pattern as today’s filesystem tools.

**Example you have in mind:** *“Write a script that fetches the latest world headlines and email them to me every day at 08:00.”*

That can work **in principle** once those tools exist and the host is set up correctly: the script would use your existing mail path (e.g. Microsoft Graph / SMTP) with credentials from the environment or a secrets file the cron user can read; the schedule would trigger that script on the VPS. In practice you still need **clear boundaries** (sandbox, reviewed code, rate limits, maybe human approval before installing a cron line) so arbitrary automation does not become a remote-code-execution footgun.

## Source layout

- `src/core/` — `config` (includes `load_config` + startup check/reset helpers), `microsoft_agent` (runtime JSON + MSAL), LLM + MCP loop, session memory, shared chat phrases.
- `src/main.py`, `src/telegram_bot.py` — CLI and Telegram entrypoints.
- `deploy/` — `patch_jarvis1net_env.py` (dopisuje brakujące klucze w `.env`); `diag_microsoft_vps.py` (szybka diagnostyka Microsoft/MSAL, uruchom z katalogu agenta).
