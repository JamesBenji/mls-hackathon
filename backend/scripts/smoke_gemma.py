"""One-shot live smoke test of the Gemma cloud adapter.

Run from backend/ (reads .env there):  .venv/Scripts/python.exe scripts/smoke_gemma.py
Hits the real API: health probe, latency probe, and one structured triage call.
"""
import asyncio

from app.adapters import gemma_client
from app.config import settings
from app.schemas.clinical import TriageResult

TRANSCRIPT = (
    "Agent: How are you feeling today?\n"
    "Patient: Not great. My chest feels tight and I'm dizzy.\n"
    "Agent: Did you measure your blood pressure?\n"
    "Patient: Yes, it was 185 over 120 this morning."
)


async def main() -> None:
    print(f"model      : {settings.gemma_model}")
    print(f"key set    : {bool(settings.gemma_api_key)}")
    print(f"health     : {await gemma_client.health()}")
    print(f"probe      : {await gemma_client.probe_models()}")

    triage = await gemma_client.chat_json(
        settings.gemma_model,
        [{"role": "system", "content": "You are a clinical triage assistant for doctors."},
         {"role": "user", "content": TRANSCRIPT}],
        TriageResult,
    )
    print(f"triage     : {triage.model_dump()}")
    print("SMOKE OK")


if __name__ == "__main__":
    asyncio.run(main())
