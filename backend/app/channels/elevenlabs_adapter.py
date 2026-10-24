"""ElevenLabs channel adapter.

Thin adapter over ElevenLabs Conversational AI outbound calling. Keeps the
core channel-agnostic: the core hands it a patient + question plan; the
adapter deals with ElevenLabs specifics. If no API key is configured it
runs in MOCK mode so the rest of the system is testable without credentials.
"""
from __future__ import annotations

import httpx

from app.config import settings

BASE = "https://api.elevenlabs.io/v1"


def is_configured() -> bool:
    return bool(settings.elevenlabs_api_key and settings.elevenlabs_agent_id)


async def start_outbound_call(to_number: str, question_plan: list[str],
                              patient_name: str) -> dict:
    """Place an outbound call. Returns {status, call_id|mock, detail}."""
    if not is_configured():
        return {
            "status": "mock",
            "detail": "ELEVENLABS_API_KEY/AGENT_ID not set — simulated call.",
            "would_ask": question_plan,
            "to": to_number,
        }

    # First dynamic variable injects the plan into the agent's prompt.
    first_message = (
        f"Hello {patient_name}, this is Guardian calling on behalf of your doctor "
        f"for a quick check-in."
    )
    payload = {
        "agent_id": settings.elevenlabs_agent_id,
        "agent_phone_number_id": settings.elevenlabs_phone_number_id,
        "to_number": to_number,
        "conversation_initiation_client_data": {
            "dynamic_variables": {
                "patient_name": patient_name,
                "question_plan": "\n".join(f"- {q}" for q in question_plan),
            },
            "conversation_config_override": {
                "agent": {"first_message": first_message}
            },
        },
    }
    headers = {"xi-api-key": settings.elevenlabs_api_key}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{BASE}/convai/twilio/outbound-call", json=payload, headers=headers
        )
        if resp.status_code >= 400:
            return {"status": "error", "code": resp.status_code, "detail": resp.text}
        data = resp.json()
        return {"status": "placed", "call_id": data.get("callSid") or data.get("conversation_id"),
                "detail": data}
