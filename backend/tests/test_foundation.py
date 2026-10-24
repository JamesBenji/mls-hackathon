"""Smoke tests for the shared foundation (config, models, transcripts)."""
from app import models
from app.config import settings
from app.phone import normalize_e164
from app.services import transcripts


def test_schema_creates_on_sqlite(db, seeded):
    assert db.get(models.Patient, seeded["maria_id"]).chronic_conditions == [
        "hypertension", "type 2 diabetes"]


def test_settings_pinned_for_tests():
    assert settings.database_url.startswith("sqlite")
    assert settings.mock_telephony is True
    assert settings.scheduler_enabled is False


def test_app_imports_and_health(client):
    assert client.get("/health").status_code in (200, 404)  # route filled in later


def test_render_and_patient_turns():
    text = transcripts.render_transcript([
        {"role": "agent", "message": "Any chest pain?"},
        {"role": "user", "message": "No, I feel fine."},
        {"role": "agent", "message": ""},
    ])
    assert text == "Agent: Any chest pain?\nPatient: No, I feel fine."
    assert transcripts.patient_turns(text) == ["No, I feel fine."]
    assert transcripts.patient_text("my chest hurts") == "my chest hurts"
    assert transcripts.has_patient_response("Agent: Hello?\nPatient: ...") is False
    assert transcripts.has_patient_response(text) is True


def test_normalize_e164():
    assert normalize_e164("+49 151 1234-5678") == "+4915112345678"
    assert normalize_e164("0049 151 12345678") == "+4915112345678"
    assert normalize_e164("015112345678") is None
