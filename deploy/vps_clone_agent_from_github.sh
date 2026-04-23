#!/usr/bin/env bash
set -euo pipefail
# Run on VPS as jump: restore the agent from GitHub and keep .env and logs/.

REPO_URL="${JARVIS1NET_REPO_URL:-https://github.com/marionet1/jarvis1net.git}"
TARGET="${JARVIS1NET_HOME:-/home/jump/jarvis1net}"
BK="${TARGET}.backup.$(date +%Y%m%d%H%M%S)"

if [[ ! -d "$TARGET" ]]; then
  echo "Missing directory $TARGET - performing fresh clone."
  git clone --depth 1 "$REPO_URL" "$TARGET"
  cd "$TARGET"
  python3 -m venv .venv
  .venv/bin/pip install -q -U pip
  .venv/bin/python -c "import json,subprocess,sys; d=json.load(open('requirements.json')); subprocess.check_call([sys.executable,'-m','pip','install',*d['python_dependencies']])"
  exit 0
fi

mv "$TARGET" "$BK"
git clone --depth 1 "$REPO_URL" "$TARGET"
if [[ -f "$BK/.env" ]]; then cp "$BK/.env" "$TARGET/.env"; fi
mkdir -p "$TARGET/logs"
if [[ -d "$BK/logs" ]]; then cp -a "$BK/logs/." "$TARGET/logs/" 2>/dev/null || true; fi

cd "$TARGET"
python3 -m venv .venv
.venv/bin/pip install -q -U pip
.venv/bin/python -c "import json,subprocess,sys; d=json.load(open('requirements.json')); subprocess.check_call([sys.executable,'-m','pip','install',*d['python_dependencies']])"

systemctl --user restart jarvis1net-telegram.service || true
echo "OK. Backup: $BK"
