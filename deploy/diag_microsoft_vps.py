#!/usr/bin/env python3
"""Run on VPS from repo root: .venv/bin/python deploy/diag_microsoft_vps.py"""
import os
import sys
from pathlib import Path

# jarvis1net/src on PYTHONPATH
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
os.chdir(Path(__file__).resolve().parent.parent / "src")

from core.config import load_config  # noqa: E402
from core.microsoft_agent import read_settings, resolve_graph_access_token, settings_path  # noqa: E402


def main() -> None:
    c = load_config()
    audit_parent = Path(c.audit_log_path).expanduser().resolve().parent
    cache = Path(c.microsoft_token_cache_path).expanduser()
    print("effective_client_id_len:", len(c.microsoft_client_id.strip()))
    print("tenant:", c.microsoft_tenant_id)
    print("scopes_count:", len(c.microsoft_graph_scopes))
    print("token_cache_path_exists:", cache.exists())
    print("runtime_settings_path:", settings_path(c.audit_log_path))
    rt = read_settings(c.audit_log_path)
    cid = str(rt.get("client_id") or "")
    print("runtime_has_client_id:", bool(cid), "suffix:", cid[-4:] if len(cid) >= 4 else "")
    tok = resolve_graph_access_token(c)
    print("resolve_graph_token:", "yes" if tok else "no")
    if not tok:
        print("HINT: run /microsoft-login in Telegram and finish browser sign-in (creates ms_graph_token_cache.json).")


if __name__ == "__main__":
    main()
