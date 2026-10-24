"""Test doubles shared across the suite."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx


class FakeOllama:
    """In-process fake of Ollama's HTTP API on top of httpx.MockTransport.

    Usage:
        fake.queue_chat('{"questions": [...]}')      # next /api/chat reply(s)
        fake.chat_handler = lambda body: "text"     # or compute from request
        fake.fail = httpx.ConnectError("down")      # every request raises
        fake.delay_s = 10                           # sleep before replying
    Every /api/chat request body is recorded in fake.chat_requests.
    """

    def __init__(self) -> None:
        self.chat_queue: list[str | Exception] = []
        self.chat_handler: Callable[[dict], str] | None = None
        self.chat_requests: list[dict] = []
        self.fail: Exception | None = None
        self.delay_s: float = 0.0
        self.tags: list[str] = ["medgemma:4b", "gemma4:12b"]
        self.running: list[dict] = [
            {"name": "medgemma:4b", "model": "medgemma:4b", "size": 100, "size_vram": 100},
            {"name": "gemma4:12b", "model": "gemma4:12b", "size": 100, "size_vram": 100},
        ]

    def queue_chat(self, *contents: str | Exception) -> None:
        self.chat_queue.extend(contents)

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.fail is not None:
            raise self.fail
        path = request.url.path
        if path == "/api/chat":
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
                "model": body.get("model"),
                "message": {"role": "assistant", "content": content},
                "done": True,
            })
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": t, "model": t} for t in self.tags]})
        if path == "/api/ps":
            return httpx.Response(200, json={"models": self.running})
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.22.0"})
        return httpx.Response(404, json={"error": f"unknown path {path}"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)


def chat_json_reply(obj: Any) -> str:
    """Serialize a dict / Pydantic model the way Ollama returns JSON content."""
    if hasattr(obj, "model_dump_json"):
        return obj.model_dump_json()
    return json.dumps(obj)
