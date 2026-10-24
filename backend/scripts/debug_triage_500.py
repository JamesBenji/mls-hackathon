"""Diagnose the live HTTP 500 on the smoke test's structured triage call.

The health probe (8-token cap, no schema text) succeeds on
gemma-4-26b-a4b-it, but the triage chat_json call 500s. This script
separates the three candidate causes:

  1. transient Google-side 500s  -> the exact payload passes on a retry
  2. the schema text in the prompt -> passes without it, fails with it
  3. the missing maxOutputTokens cap  -> passes with a cap, fails without

It replays the EXACT payload gemma_client builds by calling the client's
own _chat_payload (a private helper, used here deliberately so the replay
cannot drift from what production sends), then toggles one variable at a
time. Raw status codes are printed; the client's error mapping is bypassed.

Run from backend/:  .venv/Scripts/python.exe -m scripts.debug_triage_500
"""
import asyncio

import httpx

from app.adapters import gemma_client
from app.config import settings
from app.schemas.clinical import TriageResult

TRANSCRIPT = (
    "Agent: How are you feeling today?\n"
    "Patient: Not great. My chest feels tight and I'm dizzy.\n"
    "Agent: Did you measure your blood pressure?\n"
    "Patient: Yes, it was 185 over 120 this morning."
)
WITH_SYSTEM = [
    {"role": "system", "content": "You are a clinical triage assistant for doctors."},
    {"role": "user", "content": TRANSCRIPT},
]
NO_SYSTEM = [{"role": "user", "content": TRANSCRIPT}]


async def fire(label: str, payload: dict) -> None:
    """One raw POST; prints status, latency and a reply snippet."""
    model = settings.gemma_model
    url = f"{settings.gemma_base_url}/models/{model}:generateContent"
    started = asyncio.get_event_loop().time()
    try:
        async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
            resp = await client.post(url, json=payload,
                                     headers={"x-goog-api-key": settings.gemma_api_key})
    except Exception as exc:  # noqa: BLE001 - diagnostics print everything
        print(f"ERR  {label:38s} {type(exc).__name__}: {exc}")
        return
    ms = (asyncio.get_event_loop().time() - started) * 1000
    body = resp.json()
    text = ""
    cands = body.get("candidates") or []
    if cands:
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts)
    err = (body.get("error") or {}).get("message", "") if resp.status_code != 200 else ""
    size = len(str(payload))
    print(f"{resp.status_code}  {label:38s} {ms:6.0f} ms  payload={size}B  "
          f"{(text or err)[:90]!r}")


def payload(messages, *, schema, temperature=0, num_predict=None) -> dict:
    return gemma_client._chat_payload(
        settings.gemma_model, messages, schema=schema, temperature=temperature,
        images_b64=None, num_predict=num_predict)


async def main() -> None:
    model = settings.gemma_model
    print(f"model: {model}")
    schema = TriageResult.model_json_schema()

    exact = payload(WITH_SYSTEM, schema=schema)  # the config that 500'd

    print("\n-- 1) transience: the exact failing payload, 3 attempts --")
    for i in range(1, 4):
        await fire(f"exact attempt {i}", exact)
        if i < 3:
            await asyncio.sleep(2)

    print("\n-- 2) bisection: toggle one variable at a time --")
    await fire("no schema text (plain chat)", payload(WITH_SYSTEM, schema=None))
    await asyncio.sleep(1)
    await fire("schema + maxOutputTokens=512",
               payload(WITH_SYSTEM, schema=schema, num_predict=512))
    await asyncio.sleep(1)
    await fire("schema + temperature=0.4",
               payload(WITH_SYSTEM, schema=schema, temperature=0.4))
    await asyncio.sleep(1)
    await fire("schema, system prompt removed",
               payload(NO_SYSTEM, schema=schema))


if __name__ == "__main__":
    asyncio.run(main())
