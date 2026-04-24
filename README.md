# jarvis1net

Lekki asystent (OpenRouter) z opcjonalnym **Telegramem** / **CLI** i narzędziami **MCP** — tylko transport **stdio** do [`mcp-jarvis1net`](https://github.com/marionet1/mcp-jarvis1net).

**Pełny stos Docker (zalecany), ścieżki, VPS:** [README nadrzędnego repozytorium jarvis-stack](https://github.com/marionet1/jarvis-stack/blob/main/README.md) (katalog nadrzędny w monoreo: `../README.md` lokalnie).

## Status (aktualny)

| | |
|--|--|
| **MCP** | Tylko **stdio** — `node` + ścieżka do `mcp-jarvis1net/dist/index.js` (`MCP_STDIO_ARGS` w `.env`). Tryb HTTP usunięty. |
| **Produkcja** | [jarvis-stack](https://github.com/marionet1/jarvis-stack): jeden obraz Docker z agentem + zbudowanym MCP w `/opt/mcp-jarvis1net`. |
| **Python** | 3.12+ |
| **MCP lokalnie** | Osobno: **Node 20+** + **npm** w katalogu `mcp-jarvis1net` — `npm install` / `npm run build` |

---

## Co robi

- Odpowiedzi konwersacyjne przez **OpenRouter** (CLI: `src/main.py`, Telegram: `src/telegram_bot.py`).
- **Narzędzia MCP** — podproces Node; token Graph do `microsoft_*` w argumencie `graph_access_token`.
- **Krótka pamięć** sesji (per CLI / per czat Telegram), pliki JSON obok katalogu audytu.
- **Raport startowy** (Telegram + stdout): OpenRouter, MCP, Graph.
- **Sekrety z czatu:** `openrouter_api_key` → `jarvis_runtime_secrets.json` (`/jarvis-set-openrouter-key`); w Dockerze pod `/app/data`.
- **Strefa czasu** `DISPLAY_TIMEZONE` (IANA) w promptach dla maili/kalendarza z Graph.
- **Opcjonalny koszt** w stopce (`OPENROUTER_SHOW_COST_ESTIMATE`).
- **Audyt** w JSONL.

## Wymagania

- Python 3.12+
- Klucz OpenRouter (`.env` albo `/jarvis-set-openrouter-key`)
- Przy pracy **bez** obrazu Docker, obok: zbudowany **`mcp-jarvis1net`** (Node 20+ / npm) — zob. [sekcję poniżej](#quick-start-lokalnie-agent--mcp) albo użyj paczki `npx -y mcp-jarvis1net` tylko dla serwera MCP w innym kliencie (Cursor).

---

## Docker: pełny stos (agent + MCP w jednym obrazie)

W katalogu **nadrzędnym** do tego (repo **jarvis-stack**), gdzie leżą równolegle `jarvis1net/`, `mcp-jarvis1net/`, `Dockerfile`:

```bash
cp jarvis1net/.env.example .env
# uzupełnij m.in. TELEGRAM_BOT_TOKEN, OPENROUTER, TELEGRAM_ALLOWED_CHAT_IDS
docker compose build && docker compose up -d
```

- Obraz: MCP w `/opt/mcp-jarvis1net`, domyślny `CMD` = `python3 src/telegram_bot.py`, dane: wolumen → `/app/data`.
- Zamiast Telegrama w tym obrazie: `docker compose run --rm jarvis python3 src/main.py`

Szczegóły: [jarvis-stack README](https://github.com/marionet1/jarvis-stack/blob/main/README.md).

---

## Quick start lokalnie (tylko agent, bez tego klonu jako root)

Venv + zależności z `requirements.json`, kopia `.env.example` → `.env` **w tym katalogu** (`jarvis1net/`). Nadal **musisz** wskazać działający MCP w `.env` (`MCP_STDIO_ARGS`), chyba że testujesz tylko fragmenty bez narzędzi.

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
python3 -c "import json,subprocess,sys; d=json.load(open('requirements.json',encoding='utf-8')); subprocess.check_call([sys.executable,'-m','pip','install',*d['python_dependencies']])"
cp .env.example .env
python src/main.py
```

Windows (PowerShell): to samo z `py` zamiast `python3` i odpowiednią aktywacją venv.

## Quick start lokalnie: agent + MCP (dwa katalogi / monoreo)

1. Klon [jarvis-stack](https://github.com/marionet1/jarvis-stack) **z submodułami** (albo sklonuj `mcp-jarvis1net` obok `jarvis1net`).
2. W `mcp-jarvis1net/`: `npm install` i `npm run build` (wymaga **npm** i **Node 20+**).
3. W `jarvis1net/.env` ustaw m.in.:

   `MCP_STDIO_ARGS=["/absolutna/ścieżka/do/mcp-jarvis1net/dist/index.js"]`

4. Uruchom agenta: `python src/telegram_bot.py` lub `python src/main.py`.

W obrazie Docker te ścieżki są już zestawione — nie trzeba ręcznie budować MCP na hoście.

---

## Telegram

```bash
python src/telegram_bot.py
```

Przy `TELEGRAM_NOTIFY_ON_START=1` dozwolone czaty dostają komunikat startowy i raport konfiguracji.

## Przydatne komendy (Telegram; część działa w CLI)

| Komenda | Działanie |
|--------|-----------|
| `/start`, `/help` | Pomoc |
| `/info` | HTML: komendy + lista narzędzi MCP |
| `/jarvis-config-check` | OpenRouter, MCP, Graph |
| `/jarvis-set-openrouter-key …` | Zapis klucza OpenRouter |
| `/jarvis-config-reset` | Czyści pliki runtime, MSAL, pamięć (jak przy `/restart` — lista dozwolonych czatów) |
| `/restart` | Restart procesu (gdy dozwolone) |
| `/jarvis-limits` | Limity MCP, strefa czasu, itd. |
| `/microsoft-*` | Microsoft Graph (device code itd.) |

**Bezpieczeństwo:** pusty `TELEGRAM_ALLOWED_CHAT_IDS` = każdy z linkiem do bota może pisać (tylko bootstrap / testy). Produkcja: ustaw listę `chat_id`.

## MCP (stdio)

`MCP_STDIO_COMMAND=node` oraz `MCP_STDIO_ARGS` = JSON z jednym elementem — pełna ścieżka do `dist/index.js` (albo `MCP_STDIO_NODE_SCRIPT`). W Dockerze: `/opt/mcp-jarvis1net/dist/index.js`.

Gdy token Graph jest ustawiony, narzędzie `microsoft_integration_status` jest odfiltrowywane (mniej szumu w manifeście).

## Zmienne środowiskowe (skrót)

Pełny opis: [.env.example](.env.example).

- OpenRouter: `OPENROUTER_API_KEY`, `MODEL`, `OPENROUTER_SHOW_COST_ESTIMATE`
- Telegram: `TELEGRAM_*`, `TELEGRAM_ALLOWED_CHAT_IDS`, …
- Ścieżki: `AUDIT_LOG_PATH`, `SESSION_CONTEXT_PATH` (opcjonalnie)
- MCP: `MCP_STDIO_COMMAND`, `MCP_STDIO_ARGS` / `MCP_STDIO_NODE_SCRIPT`, `MCP_ALLOWED_ROOTS`, limity `MCP_*`
- `DISPLAY_TIMEZONE` — IANA, np. `Europe/Warsaw`
- Microsoft: `MICROSOFT_*` (device code, MSAL) — [README nadrzędny + Azure poniżej](https://github.com/marionet1/jarvis-stack)

## Microsoft Azure (device code) — checklista

1. **Typ konta** w rejestracji aplikacji musi odpowiadać użyciu. Domyślnie: **`consumers`** (konto prywatne MSA) + redirect `…/consumers/oauth2/nativeclient`. Tylko konto służbowe: `MICROSOFT_TENANT_ID=organizations` lub `/microsoft-set-client <UUID> organizations` i redirect `…/organizations/…`.
2. **Authentication → Allow public client flows:** **Yes**.
3. **Platform „Mobile and desktop applications”:** zarejestruj **dokładnie jeden** redirect URI zgodny z `MICROSOFT_TENANT_ID` (jak w `/microsoft-show-settings`):  
   `https://login.microsoftonline.com/<tenant>/oauth2/nativeclient`  
   gdzie `<tenant>` to `common`, `organizations`, `consumers` albo **GUID** katalogu. **Nie** rejestruj naraz wielu segmentów (`common` + `organizations` + `consumers`) — przy `authority=common` Microsoft potrafi przekierować na inny host z **niepełnym** żądaniem (`response_type` / 404). Konto służbowe: tylko `…/organizations/…`; prywatne: tylko `…/consumers/…`; mieszane: tylko `…/common/…`.
4. **Usuń** nieużywane **Web** redirect URI — wystarczy **Mobile and desktop** (nativeclient) zgodnie z pkt 3.
5. **Uprawnienia (Delegated):** co najmniej `User.Read`, `Mail.ReadWrite`, `Mail.Send`, `Calendars.ReadWrite`, `Files.ReadWrite.All` — **admin consent** jeśli tenant wymaga.
6. **Manifest (opcjonalnie):** `allowPublicClient` = **true**, jeśli przełącznik w UI nie trzyma się.
7. Po zmianach w Azure: **`/microsoft-logout`** → **`/microsoft-login`**. Ostrzeżenia o „phishingu” na URL-ach z `error=` to często false positive.
8. **Brave / adblocki:** tymczasowo odblokuj `microsoft.com` i `login.microsoftonline.com` (albo użyj Chrome/Edge) — potrafią zepsuć redirecty OAuth.
9. Gdy **`invalid_request`** na `nativeclient` nie znika: ustaw w Azure tenant jako **GUID** katalogu i ten sam GUID w agencie jako `MICROSOFT_TENANT_ID` + redirect `…/<GUID>/oauth2/nativeclient`.

## Układ katalogu

- `src/core/` — config, LLM, MCP, MSAL, sesja
- `src/main.py`, `src/telegram_bot.py` — wejścia CLI / Telegram
- `deploy/` — m.in. `patch_jarvis1net_env.py` (dopisuje brakujące klucze w `.env` na serwerze), `diag_microsoft_vps.py`

## Licencja

Zgodnie z polityką repozytorium / głównego stosu (np. [jarvis-stack](https://github.com/marionet1/jarvis-stack)).
