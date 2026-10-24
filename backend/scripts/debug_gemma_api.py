"""Probe the live generateContent endpoint with payload variants to find
what Gemma 4 accepts. Run from backend/: python -m scripts.debug_gemma_api
"""
import asyncio
import os

import httpx

# Read the key from backend/.env without importing the app.
KEY = ""
with open(".env", encoding="utf-8") as f:
    for line in f:
        if line.startswith("GOOGLE_API_KEY="):
            KEY = line.split("=", 1)[1].strip()

BASE = "https://generativelanguage.googleapis.com/v1beta"
MODELS = ["gemma-4-31b-it", "gemma-4-26b-a4b-it"]


async def try_call(name: str, model: str, payload: dict, *, header: bool = True) -> None:
    url = f"{BASE}/models/{model}:generateContent"
    headers = {"x-goog-api-key": KEY} if header else {}
    if not header:
        url += f"?key={KEY}"
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=payload, headers=headers)
        body = resp.json()
        text = ""
        cands = body.get("candidates") or []
        if cands:
            parts = (cands[0].get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts)[:60]
        print(f"{resp.status_code}  {name:34s} [{model}] {text or body.get('error', '')}")
    except Exception as exc:  # noqa: BLE001
        print(f"ERR  {name:34s} [{model}] {type(exc).__name__}: {exc}")


async def main() -> None:
    plain = {"contents": [{"role": "user", "parts": [{"text": "Say ok"}]}]}
    variants = [
        ("plain", plain),
        ("no role", {"contents": [{"parts": [{"text": "Say ok"}]}]}),
        ("with temperature", {"contents": [{"role": "user", "parts": [{"text": "Say ok"}]}],
                               "generationConfig": {"temperature": 0}}),
        ("with maxOutputTokens", {"contents": [{"role": "user", "parts": [{"text": "Say ok"}]}],
                                   "generationConfig": {"temperature": 0, "maxOutputTokens": 8}}),
        ("json mime", {"contents": [{"role": "user", "parts": [{"text": "Say ok"}]}],
                        "generationConfig": {"responseMimeType": "application/json"}}),
    ]
    for model in MODELS:
        for name, payload in variants:
            await try_call(name, model, payload)
    # key as query param instead of header
    await try_call("plain, key in query", MODELS[0], plain, header=False)


if __name__ == "__main__":
    asyncio.run(main())
