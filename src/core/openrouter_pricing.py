"""Estymata kosztu USD z publicznego cennika OpenRouter (`GET /api/v1/models`)."""

from __future__ import annotations

import threading
import time
from typing import Any

import requests

_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_CACHE_TTL_SEC = 3600.0

_models_lock = threading.Lock()
_models_cache: list[dict[str, Any]] | None = None
_models_cache_at: float = 0.0


def _fetch_models_list(api_key: str) -> list[dict[str, Any]]:
    """Lista modeli z pricing; cache in-process ~1 h. Klucz API opcjonalny (endpoint często działa i bez)."""
    global _models_cache, _models_cache_at
    now = time.monotonic()
    with _models_lock:
        if _models_cache is not None and (now - _models_cache_at) < _CACHE_TTL_SEC:
            return _models_cache
    headers: dict[str, str] = {}
    key = api_key.strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    resp = requests.get(_OPENROUTER_MODELS_URL, headers=headers, timeout=45)
    resp.raise_for_status()
    data = resp.json()
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("OpenRouter /models: brak tablicy `data`")
    with _models_lock:
        _models_cache = rows
        _models_cache_at = time.monotonic()
    return rows


def _pricing_for_model(models: list[dict[str, Any]], model_id: str) -> dict[str, Any] | None:
    for m in models:
        if isinstance(m, dict) and m.get("id") == model_id:
            pr = m.get("pricing")
            return pr if isinstance(pr, dict) else None
    return None


def _to_float(x: Any) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def format_openrouter_cost_line(
    *,
    api_key: str,
    model_id: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> str:
    """
    Zwraca jedną linię markdown z ~kosztem USD.

    W polu `pricing` OpenRouter podaje **USD za 1 token** (np. 1.5e-7 = 0.15 USD / 1M prompt).
    """
    if prompt_tokens <= 0 and completion_tokens <= 0:
        return ""
    try:
        models = _fetch_models_list(api_key)
    except Exception as exc:
        return (
            f"\n- **Koszt (~)**: nie udało się pobrać `GET /api/v1/models` ({type(exc).__name__}: "
            f"{str(exc)[:180]})."
        )
    pr = _pricing_for_model(models, model_id)
    if pr is None:
        return f"\n- **Koszt (~)**: brak modelu `{model_id}` w liście OpenRouter (sprawdź `MODEL` vs id na openrouter.ai)."
    pu = _to_float(pr.get("prompt"))
    cu = _to_float(pr.get("completion"))
    if pu is None and cu is None:
        return f"\n- **Koszt (~)**: dla `{model_id}` brak pól `pricing.prompt` / `pricing.completion`."
    pu_f = pu or 0.0
    cu_f = cu or 0.0
    usd = prompt_tokens * pu_f + completion_tokens * cu_f
    per_m_in = pu_f * 1_000_000
    per_m_out = cu_f * 1_000_000
    return (
        f"\n- **Koszt orientacyjny**: ~**${usd:.4f}** USD "
        f"({prompt_tokens} prompt + {completion_tokens} completion, stawki z cennika OpenRouter dla **`{model_id}`** "
        f"~ **${per_m_in:.2f}** / 1M prompt, **${per_m_out:.2f}** / 1M completion). "
        "Faktyczne obciążenie konta może się różnić (promocje, cache, opłaty OpenRouter, zaokrąglenia)."
    )
