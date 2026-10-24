"""Test doubles shared across the suite."""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from typing import Any

import httpx

GENERATE_PATH = re.compile(r"/models/([^:]+):generateContent$")


class FakeGemma:
    """In-process fake of the Google Generative Language API (Gemma models)
    on top of httpx.MockTransport.

    Usage:
        fake.queue_chat('{"questions": [...]}')      # next generateContent reply(s)
        fake.chat_handler = lambda body: "text"       # or compute from request
        fake.fail = httpx.ConnectError("down")       # every request raises
        fake.delay_s = 10                            # sleep before replying
    Every generateContent request body is recorded in fake.chat_requests.
    """

    def __init__(self) -> None:
        self.chat_queue: list[str | Exception] = []
        self.chat_handler: Callable[[dict], str] | None = None
        self.chat_requests: list[dict] = []
        self.fail: Exception | None = None
        self.delay_s: float = 0.0
        self.models: list[str] = ["gemma-4-31b-it"]

    def queue_chat(self, *contents: str | Exception) -> None:
        self.chat_queue.extend(contents)

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.fail is not None:
            raise self.fail
        path = request.url.path
        match = GENERATE_PATH.search(path)
        if match:
            body = json.loads(request.content or b"{}")
            self.chat_requests.append(body)
            if self.chat_queue:
                item = self.chat_queue.pop(0)
                if isinstance(item, Exception):
                    raise item
                content = item
            elif self.chat_handler is not None:
                content = self.chat_handler(body)
            else:
                content = "ok"
            return httpx.Response(200, json={
                "candidates": [
                    {"content": {"role": "model", "parts": [{"text": content}]},
                     "finishReason": "STOP"}
                ],
            })
        if path.endswith("/models"):
            return httpx.Response(200, json={
                "models": [{"name": f"models/{m}"} for m in self.models],
            })
        return httpx.Response(404, json={"error": {"message": f"unknown path {path}"}})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)


def chat_json_reply(obj: Any) -> str:
    """Serialize a dict / Pydantic model as the reply text."""
    if hasattr(obj, "model_dump_json"):
        return obj.model_dump_json()
    return json.dumps(obj)
