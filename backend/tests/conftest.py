"""Shared fixtures. Environment is pinned BEFORE the app is imported so the
engine binds to an in-memory SQLite db and nothing reaches the network.
"""
from __future__ import annotations

import os

os.environ.update({
    "DATABASE_URL": "sqlite+pysqlite:///:memory:",
    "SCHEDULER_ENABLED": "false",
    "MOCK_TELEPHONY": "true",
    "GUARDIAN_API_KEY": "",
    "ELEVEN_WEBHOOK_SECRET": "test-webhook-secret",
    "ELEVEN_TOOL_AUTH_TOKEN": "test-tool-token",
    "TWILIO_AUTH_TOKEN": "test-twilio-token",
    "PUBLIC_BASE_URL": "https://guardian.test",
    "CALLBACK_DEFAULT_MINUTES": "15",
    "OLLAMA_URL": "http://ollama.test",
    "MEDGEMMA_MODEL": "medgemma:4b",
    "GEMMA4_MODEL": "gemma4:12b",
    "CLINICAL_PIPELINE": "two_stage",
    "PLAN_TIMEOUT_S": "8",
    "MODEL_TIMEOUT_S": "120",
    "OLLAMA_NUM_CTX": "16384",
    "OLLAMA_NUM_PARALLEL": "2",
    "TIMEZONE": "Europe/Berlin",
    # Blank every real credential so a developer's backend/.env can never
    # leak into tests (env vars take precedence over the .env file).
    "ELEVEN_API_KEY": "",
    "ELEVEN_AGENT_ID": "",
    "ELEVEN_AGENT_PHONE_NUMBER_ID": "",
    "TWILIO_ACCOUNT_SID": "",
    "TWILIO_API_KEY_SID": "",
    "TWILIO_API_KEY_SECRET": "",
    "TWILIO_NUMBER": "",
    "EMAIL_API_KEY": "",
})

from datetime import date, timedelta  # noqa: E402

import pytest  # noqa: E402

from app import models  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.timeutil import utcnow  # noqa: E402
from tests.fakes import FakeOllama  # noqa: E402

WEBHOOK_SECRET = "test-webhook-secret"
TOOL_TOKEN = "test-tool-token"
TWILIO_TOKEN = "test-twilio-token"
PUBLIC_BASE_URL = "https://guardian.test"


@pytest.fixture(autouse=True)
def _schema():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _reset_singletons():
    yield
    from app.adapters import elevenlabs, ollama_client
    from app.services import scheduler
    for fn in (lambda: ollama_client.set_transport(None),
               lambda: elevenlabs.set_transport(None),
               scheduler.reset_scheduler):
        try:
            fn()
        except NotImplementedError:  # module not implemented yet
            pass


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def fake_ollama():
    from app.adapters import ollama_client
    fake = FakeOllama()
    ollama_client.set_transport(fake.transport())
    yield fake
    ollama_client.set_transport(None)


@pytest.fixture
def seeded(db):
    """One doctor, Maria (high risk, new prescription, prior report) and
    John (no history). Returns ids."""
    now = utcnow()
    doc = models.Doctor(name="Dr. Lee", email="lee@example.com")
    db.add(doc)
    db.flush()
    maria = models.Patient(
        doctor_id=doc.id, name="Maria Gomez", phone="+15550000001",
        dob=date(1958, 4, 12), blood_type="O+", height_cm=162, weight_kg=78,
        chronic_conditions=["hypertension", "type 2 diabetes"],
        category="post-op", risk_level="high",
    )
    john = models.Patient(
        doctor_id=doc.id, name="John Carter", phone="+15550000002",
        chronic_conditions=["hypertension"], category="chronic", risk_level="medium",
    )
    db.add_all([maria, john])
    db.flush()
    db.add_all([
        models.Vital(patient_id=maria.id, type="bp_systolic", value=158, unit="mmHg",
                     source="call", recorded_at=now - timedelta(days=6)),
        models.Vital(patient_id=maria.id, type="bp_diastolic", value=95, unit="mmHg",
                     source="call", recorded_at=now - timedelta(days=6)),
        models.Vital(patient_id=maria.id, type="blood_sugar", value=180, unit="mg/dL",
                     source="call", recorded_at=now - timedelta(days=1)),
        models.Medication(patient_id=maria.id, name="Lisinopril", dose="10mg",
                          schedule="once daily", is_new_prescription=True,
                          started_at=date.today() - timedelta(days=5)),
        models.Medication(patient_id=maria.id, name="Metformin", dose="500mg",
                          schedule="twice daily", is_new_prescription=False),
        models.QuestionTemplate(doctor_id=doc.id, patient_id=maria.id, questions=[
            "Did you take your blood pressure today?",
            "Did you check your blood sugar? What was the reading?",
            "Did you take your medication today, and at what time?",
        ]),
    ])
    prior = models.CallSession(patient_id=maria.id, direction="outbound", channel="voice",
                               status="completed", attempt=1,
                               started_at=now - timedelta(days=6),
                               ended_at=now - timedelta(days=6))
    db.add(prior)
    db.flush()
    db.add(models.CallReport(
        call_session_id=prior.id, patient_id=maria.id, status="ok",
        summary="BP high (158/95). Reports occasional dizziness. Mild cough.",
        structured_data={"bp_systolic": 158, "bp_diastolic": 95, "symptoms": ["cough"]},
        risk_flags=["high_bp"], escalated=False,
        created_at=now - timedelta(days=6),
    ))
    db.commit()
    return {"doctor_id": doc.id, "maria_id": maria.id, "john_id": john.id,
            "prior_session_id": prior.id}
