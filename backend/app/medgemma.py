"""MedGemma client via Ollama.

Channel-agnostic helper the core uses for reasoning. Falls back to a
clear error message (not a crash) if Ollama/MedGemma isn't reachable,
so the rest of the API still works during local dev.
"""
from __future__ import annotations

import httpx

from app.config import settings


async def generate(prompt: str, system: str | None = None) -> str:
    """Single-shot completion from MedGemma via the Ollama API."""
    payload: dict = {
        "model": settings.medgemma_model,
        "prompt": prompt,
        "stream": False,
    }
    if system:
        payload["system"] = system
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{settings.medgemma_url}/api/generate", json=payload
            )
            resp.raise_for_status()
            return resp.json().get("response", "")
    except Exception as exc:  # noqa: BLE001 - surface a readable message in dev
        return f"[medgemma unavailable: {exc}]"


async def health() -> dict:
    """Report whether Ollama is up and the model is present."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{settings.medgemma_url}/api/tags")
            resp.raise_for_status()
            models = [m.get("name", "") for m in resp.json().get("models", [])]
            return {
                "ollama": "up",
                "model_present": any(
                    settings.medgemma_model in m for m in models
                ),
                "models": models,
            }
    except Exception as exc:  # noqa: BLE001
        return {"ollama": "down", "error": str(exc)}
