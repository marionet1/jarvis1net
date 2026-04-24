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


def estimate_openrouter_usd(api_key: str, model_id: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    """USD z pól `pricing.prompt` / `pricing.completion` (USD za 1 token). None = brak danych / błąd."""
    if prompt_tokens <= 0 and completion_tokens <= 0:
        return None
    try:
        models = _fetch_models_list(api_key)
        pr = _pricing_for_model(models, model_id)
        if not pr:
            return None
        pu = _to_float(pr.get("prompt"))
        cu = _to_float(pr.get("completion"))
        if pu is None and cu is None:
            return None
        return prompt_tokens * (pu or 0.0) + completion_tokens * (cu or 0.0)
    except Exception:
        return None


def _format_usd_compact(usd: float) -> str:
    if usd <= 0:
        return "$0"
    if usd < 0.01:
        return f"${usd:.4f}"
    if usd < 1:
        return f"${usd:.3f}"
    return f"${usd:.2f}"


def build_compact_token_usage_footer(
    *,
    api_key: str,
    model_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    model_rounds: int,
    show_cost_estimate: bool,
    limit_hit: bool = False,
) -> str:
    """
    Jedna zwięzła linia: ``Tokens: prompt+completion=total est ~$...`` (+ opcjonalnie rundy / limit).
    """
    if prompt_tokens <= 0 and completion_tokens <= 0:
        return (
            "\n\n- Tokeny: brak pola usage w odpowiedziach API (OpenRouter czasem nie zwraca usage)."
        )
    total = prompt_tokens + completion_tokens
    parts = [f"Tokens: {prompt_tokens}+{completion_tokens}={total}"]
    if model_rounds > 1:
        parts.append(f"{model_rounds} calls")
    line = "\n\n- " + ", ".join(parts)
    if show_cost_estimate:
        usd = estimate_openrouter_usd(api_key, model_id, prompt_tokens, completion_tokens)
        if usd is not None:
            line += f" est {_format_usd_compact(usd)}"
        else:
            line += " est n/a"
    if limit_hit:
        line += " | MCP_MAX_TOOL_ROUNDS"
    return line
