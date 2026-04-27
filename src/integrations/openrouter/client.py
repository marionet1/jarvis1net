from __future__ import annotations

from openai import OpenAI

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def normalize_model_name(model: str) -> str:
    if "/" not in model:
        return f"openai/{model}"
    return model


def build_openrouter_client(api_key: str) -> OpenAI:
    return OpenAI(
        api_key=api_key,
        base_url=OPENROUTER_BASE_URL,
    )
