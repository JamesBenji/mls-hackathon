"""app.seed runs on the in-memory SQLite db and creates the demo rows."""
from __future__ import annotations

from sqlalchemy import func, select

from app import models, seed, timeutil
from app.phone import is_e164


def _count(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


def test_seed_creates_expected_rows(db):
    seed.reset_schema()
    ids = seed.seed(db)
    db.expire_all()

    assert _count(db, models.Doctor) == 1
    doctor = db.get(models.Doctor, ids["doctor_id"])
    assert doctor.name == "Dr. Lee"
    patients = db.scalars(select(models.Patient).order_by(models.Patient.id)).all()
    assert 4 <= len(patients) <= 5
    assert {p.category for p in patients} >= {"post-op", "chronic", "recovery"}
    assert all(is_e164(p.phone) and p.phone.startswith("+1555") for p in patients)
    assert len({p.phone for p in patients}) == len(patients)  # SMS matching needs unique

    by_name = {p.name: p for p in patients}
    maria, john, amina = by_name["Maria Gomez"], by_name["John Carter"], by_name["Amina Diallo"]
    assert maria.risk_level == "high" and maria.category == "post-op"
    assert set(maria.chronic_conditions) == {"hypertension", "type 2 diabetes"}
    assert john.chronic_conditions == ["hypertension"] and john.category == "chronic"
    assert amina.risk_level == "low" and amina.category == "recovery"

    meds = db.scalars(select(models.Medication).where(
        models.Medication.patient_id == maria.id)).all()
    assert any(m.name == "Lisinopril" and m.is_new_prescription for m in meds)

    # Vitals are naive UTC in the past.
    vitals = db.scalars(select(models.Vital).where(models.Vital.patient_id == maria.id)).all()
    assert len(vitals) >= 3
    assert all(v.recorded_at.tzinfo is None and v.recorded_at < timeutil.utcnow() for v in vitals)

    # Prior completed call + ok report = agent memory.
    [maria_report] = db.scalars(select(models.CallReport).where(
        models.CallReport.patient_id == maria.id)).all()
    assert maria_report.status == "ok"
    assert "cough" in maria_report.summary.lower() and "bp" in maria_report.summary.lower()
    session = db.get(models.CallSession, maria_report.call_session_id)
    assert (session.status, session.attempt, session.direction) == ("completed", 1, "outbound")
    assert len(ids["prior_session_ids"]) >= 2

    # Templates: doctor default (patient_id NULL) + Maria's own.
    templates = db.scalars(select(models.QuestionTemplate)).all()
    assert any(t.patient_id is None and t.questions for t in templates)
    maria_tpl = next(t for t in templates if t.patient_id == maria.id)
    assert "Did you take your blood pressure today?" in maria_tpl.questions

    upcoming = db.scalars(select(models.Appointment).where(
        models.Appointment.scheduled_at > timeutil.utcnow())).all()
    assert any(a.patient_id == maria.id for a in upcoming)

    # Seeded triage is a draft for the doctor.
    assert all(not t.doctor_reviewed for t in db.scalars(select(models.TriageDecision)))


def test_seed_twice_after_reset(db):
    seed.reset_schema()
    seed.seed(db)
    seed.reset_schema()
    db.expire_all()
    ids = seed.seed()  # own session
    assert ids["doctor_id"] and len(ids["patient_ids"]) >= 4
    assert _count(db, models.Doctor) == 1


def test_seeded_data_serves_dashboard(client, db):
    seed.seed(db)
    rows = client.get("/patients").json()
    maria = next(r for r in rows if r["name"] == "Maria Gomez")
    detail = client.get(f"/patients/{maria['id']}").json()
    assert detail["reports"] and detail["appointments"] and detail["medications"]
    assert client.get("/triage").json()
