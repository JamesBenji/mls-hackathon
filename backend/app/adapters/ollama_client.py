"""Model gateway: thin async wrapper over Ollama's HTTP API.
No clinical logic in here (MEDGEMMA_DO_SETUP.md §5).

CONTRACT (implemented by the ollama-client task; other modules rely on it):

    class ModelError(Exception)                 base
    class ModelUnavailable(ModelError)          connection error, timeout, HTTP 5xx,
                                                model not found (404) -> callers fall back
    class ModelOutputInvalid(ModelError)        JSON / Pydantic validation still
                                                failing after the single retry

    set_transport(transport | None)             tests inject httpx.MockTransport

    async chat(model, messages, *, schema=None, temperature=0.2,
               timeout=None, images_b64=None) -> str
    async chat_json(model, messages, schema_model, **kw) -> BaseModel
    async list_tags() -> list[str]              GET /api/tags  model names
    async list_running() -> list[dict]          GET /api/ps    models entries
    async probe_models() -> dict                for GET /health/models
    async health() -> dict                      cheap liveness for GET /health

All generations are gated by a module-level asyncio.Semaphore sized
settings.ollama_num_parallel (never more concurrent generations than
OLLAMA_NUM_PARALLEL).
"""
from __future__ import annotations

import httpx
from pydantic import BaseModel


class ModelError(Exception):
    """Base class for model gateway errors."""


class ModelUnavailable(ModelError):
    """Ollama unreachable, timed out, 5xx, or the model tag does not exist."""


class ModelOutputInvalid(ModelError):
    """Structured output failed validation even after one retry."""


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    raise NotImplementedError


async def chat(model: str, messages: list[dict], *, schema: dict | None = None,
               temperature: float = 0.2, timeout: float | None = None,
               images_b64: list[str] | None = None) -> str:
    raise NotImplementedError


async def chat_json(model: str, messages: list[dict], schema_model: type[BaseModel],
                    **kw) -> BaseModel:
    raise NotImplementedError


async def list_tags() -> list[str]:
    raise NotImplementedError


async def list_running() -> list[dict]:
    raise NotImplementedError


async def probe_models() -> dict:
    raise NotImplementedError


async def health() -> dict:
    raise NotImplementedError
