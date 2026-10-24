"""Create tables and seed FICTIONAL demo data (Postgres or SQLite).

Run from the backend/ dir:  python -m app.seed
Drops and recreates all Guardian tables, then inserts demo rows. Tests call
reset_schema() / seed(db) directly.

All people and numbers are made up (+1555... numbers are reserved for
fiction). Clinical content is demo material only; every report and triage
row is a draft that a doctor still has to review.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import database, models, timeutil

DOCTOR_DEFAULT_QUESTIONS = [
    "Did you take your blood pressure today?",
    "Did you take your medication today?",
    "How many hours did you sleep last night?",
    "How are you feeling overall today?",
]

# Maria's own template, from SCOPE.md's interview question bank.
MARIA_QUESTIONS = [
    "Did you take your blood pressure today?",
    "Did you check your blood sugar? What was the reading?",
    "What's your current weight?",
    "Did you take your medication today?",
    "What time did you take it?",
    "Any side effects since starting the new medication?",
    "How many hours did you sleep last night?",
    "How are you feeling overall today?",
]


def reset_schema() -> None:
    database.Base.metadata.drop_all(database.engine)
    database.Base.metadata.create_all(database.engine)
    # APScheduler keeps its jobs in its own table (not in our metadata). Stale
    # resume:<id> / start:<id> jobs would fire against the re-used ids.
    with database.engine.begin() as conn:
        if sa_inspect(conn).has_table("apscheduler_jobs"):
            conn.execute(text("DELETE FROM apscheduler_jobs"))


def _vital(patient_id: int, type_: str, value: float, unit: str, days_ago: float,
           source: str = "call") -> models.Vital:
    return models.Vital(patient_id=patient_id, type=type_, value=value, unit=unit,
                        source=source,
                        recorded_at=timeutil.utcnow() - timedelta(days=days_ago))


def _prior_call(db: Session, patient: models.Patient, *, days_ago: int, summary: str,
                structured: dict, risk_flags: list[str], proposed_risk: str | None = None,
                changes: list[str] | None = None) -> models.CallSession:
    """A completed outbound check-in (attempt 1) with its report = agent memory."""
    when = timeutil.utcnow() - timedelta(days=days_ago)
    session = models.CallSession(
        patient_id=patient.id, direction="outbound", channel="voice", status="completed",
        started_at=when, ended_at=when + timedelta(minutes=6), attempt=1,
        conversation_id=f"seed-conv-{patient.id}-{days_ago}", created_at=when,
    )
    db.add(session)
    db.flush()
    db.add(models.CallAttempt(
        call_session_id=session.id, attempt=1, conversation_id=session.conversation_id,
        resume_from="start", status="completed", created_at=when,
        ended_at=when + timedelta(minutes=6),
    ))
    db.add(models.CallReport(
        call_session_id=session.id, patient_id=patient.id, status="ok", summary=summary,
        structured_data=structured, risk_flags=risk_flags,
        changes_since_last_call=changes or [], proposed_risk_level=proposed_risk,
        escalated=False, created_at=when + timedelta(minutes=7),
        model_info={"pipeline": "seed", "models": [], "notes": ["fictional demo data"]},
    ))
    return session


def seed(db: Session | None = None) -> dict:
    """Insert the demo rows (tables must exist). Returns the created ids."""
    own = db is None
    db = db or database.SessionLocal()
    try:
        now = timeutil.utcnow()
        today = date.today()
        doc = models.Doctor(name="Dr. Lee", email="lee@example.com")
        db.add(doc)
        db.flush()
        db.add(models.QuestionTemplate(doctor_id=doc.id, patient_id=None,
                                       questions=list(DOCTOR_DEFAULT_QUESTIONS)))

        maria = models.Patient(
            doctor_id=doc.id, name="Maria Gomez", phone="+15550000001",
            dob=date(1958, 4, 12), blood_type="O+", height_cm=162, weight_kg=78,
            chronic_conditions=["hypertension", "type 2 diabetes"],
            category="post-op", risk_level="high",
        )
        john = models.Patient(
            doctor_id=doc.id, name="John Carter", phone="+15550000002",
            dob=date(1971, 9, 3), blood_type="A-", height_cm=178, weight_kg=91,
            chronic_conditions=["hypertension"], category="chronic", risk_level="medium",
        )
        amina = models.Patient(
            doctor_id=doc.id, name="Amina Diallo", phone="+15550000003",
            dob=date(1990, 1, 22), blood_type="B+", height_cm=167, weight_kg=63,
            chronic_conditions=[], category="recovery", risk_level="low",
        )
        peter = models.Patient(
            doctor_id=doc.id, name="Peter Novak", phone="+15550000004",
            dob=date(1949, 11, 30), blood_type="AB+", height_cm=172, weight_kg=84,
            chronic_conditions=["heart failure", "atrial fibrillation"],
            category="chronic", risk_level="medium",
        )
        lena = models.Patient(
            doctor_id=doc.id, name="Lena Fischer", phone="+15550000005",
            dob=date(1983, 6, 8), blood_type="A+", height_cm=170, weight_kg=66,
            chronic_conditions=["asthma"], category="post-op", risk_level="low",
        )
        db.add_all([maria, john, amina, peter, lena])
        db.flush()

        db.add_all([
            # ── Maria Gomez (high risk: hypertension + diabetes) ──────────
            # bp_systolic: 14 daily readings, trending down ~162 → ~142
            _vital(maria.id, "bp_systolic", 162, "mmHg", 13),
            _vital(maria.id, "bp_systolic", 164, "mmHg", 12),
            _vital(maria.id, "bp_systolic", 159, "mmHg", 11),
            _vital(maria.id, "bp_systolic", 161, "mmHg", 10),
            _vital(maria.id, "bp_systolic", 157, "mmHg", 9),
            _vital(maria.id, "bp_systolic", 155, "mmHg", 8),
            _vital(maria.id, "bp_systolic", 158, "mmHg", 7),
            _vital(maria.id, "bp_systolic", 153, "mmHg", 6),
            _vital(maria.id, "bp_systolic", 150, "mmHg", 5),
            _vital(maria.id, "bp_systolic", 148, "mmHg", 4),
            _vital(maria.id, "bp_systolic", 146, "mmHg", 3),
            _vital(maria.id, "bp_systolic", 149, "mmHg", 2),
            _vital(maria.id, "bp_systolic", 144, "mmHg", 1),
            _vital(maria.id, "bp_systolic", 142, "mmHg", 0),
            # bp_diastolic: 14 daily readings, trending down ~98 → ~88
            _vital(maria.id, "bp_diastolic", 98, "mmHg", 13),
            _vital(maria.id, "bp_diastolic", 99, "mmHg", 12),
            _vital(maria.id, "bp_diastolic", 96, "mmHg", 11),
            _vital(maria.id, "bp_diastolic", 97, "mmHg", 10),
            _vital(maria.id, "bp_diastolic", 95, "mmHg", 9),
            _vital(maria.id, "bp_diastolic", 94, "mmHg", 8),
            _vital(maria.id, "bp_diastolic", 96, "mmHg", 7),
            _vital(maria.id, "bp_diastolic", 93, "mmHg", 6),
            _vital(maria.id, "bp_diastolic", 91, "mmHg", 5),
            _vital(maria.id, "bp_diastolic", 90, "mmHg", 4),
            _vital(maria.id, "bp_diastolic", 89, "mmHg", 3),
            _vital(maria.id, "bp_diastolic", 91, "mmHg", 2),
            _vital(maria.id, "bp_diastolic", 88, "mmHg", 1),
            _vital(maria.id, "bp_diastolic", 87, "mmHg", 0),
            # blood_sugar: 14 daily readings, fluctuating 150-210 mg/dL
            _vital(maria.id, "blood_sugar", 198, "mg/dL", 13),
            _vital(maria.id, "blood_sugar", 185, "mg/dL", 12),
            _vital(maria.id, "blood_sugar", 210, "mg/dL", 11),
            _vital(maria.id, "blood_sugar", 172, "mg/dL", 10),
            _vital(maria.id, "blood_sugar", 195, "mg/dL", 9),
            _vital(maria.id, "blood_sugar", 163, "mg/dL", 8),
            _vital(maria.id, "blood_sugar", 180, "mg/dL", 7),
            _vital(maria.id, "blood_sugar", 192, "mg/dL", 6),
            _vital(maria.id, "blood_sugar", 155, "mg/dL", 5),
            _vital(maria.id, "blood_sugar", 201, "mg/dL", 4),
            _vital(maria.id, "blood_sugar", 176, "mg/dL", 3),
            _vital(maria.id, "blood_sugar", 168, "mg/dL", 2),
            _vital(maria.id, "blood_sugar", 189, "mg/dL", 1),
            _vital(maria.id, "blood_sugar", 174, "mg/dL", 0),
            # weight: every 2-3 days, stable 77-79 kg
            _vital(maria.id, "weight", 78.2, "kg", 13, source="manual"),
            _vital(maria.id, "weight", 77.8, "kg", 11, source="manual"),
            _vital(maria.id, "weight", 78.5, "kg", 9, source="manual"),
            _vital(maria.id, "weight", 77.6, "kg", 7, source="manual"),
            _vital(maria.id, "weight", 78.1, "kg", 5, source="manual"),
            _vital(maria.id, "weight", 78.8, "kg", 3, source="manual"),
            _vital(maria.id, "weight", 78.0, "kg", 0, source="manual"),
            # sleep_hours: 14 daily readings, varying 4.5-7.5h
            _vital(maria.id, "sleep_hours", 5.0, "h", 13),
            _vital(maria.id, "sleep_hours", 6.5, "h", 12),
            _vital(maria.id, "sleep_hours", 4.5, "h", 11),
            _vital(maria.id, "sleep_hours", 5.5, "h", 10),
            _vital(maria.id, "sleep_hours", 7.0, "h", 9),
            _vital(maria.id, "sleep_hours", 6.0, "h", 8),
            _vital(maria.id, "sleep_hours", 5.5, "h", 7),
            _vital(maria.id, "sleep_hours", 4.5, "h", 6),
            _vital(maria.id, "sleep_hours", 6.5, "h", 5),
            _vital(maria.id, "sleep_hours", 7.5, "h", 4),
            _vital(maria.id, "sleep_hours", 5.0, "h", 3),
            _vital(maria.id, "sleep_hours", 6.0, "h", 2),
            _vital(maria.id, "sleep_hours", 7.0, "h", 1),
            _vital(maria.id, "sleep_hours", 5.5, "h", 0),

            # ── John Carter (medium risk: hypertension) ───────────────────
            # bp_systolic: 10 readings over 14 days, stable 132-142
            _vital(john.id, "bp_systolic", 138, "mmHg", 13),
            _vital(john.id, "bp_systolic", 142, "mmHg", 12),
            _vital(john.id, "bp_systolic", 136, "mmHg", 10),
            _vital(john.id, "bp_systolic", 140, "mmHg", 8),
            _vital(john.id, "bp_systolic", 134, "mmHg", 7),
            _vital(john.id, "bp_systolic", 139, "mmHg", 5),
            _vital(john.id, "bp_systolic", 132, "mmHg", 4),
            _vital(john.id, "bp_systolic", 137, "mmHg", 2),
            _vital(john.id, "bp_systolic", 141, "mmHg", 1),
            _vital(john.id, "bp_systolic", 135, "mmHg", 0),
            # bp_diastolic: 10 readings, stable 84-90
            _vital(john.id, "bp_diastolic", 88, "mmHg", 13),
            _vital(john.id, "bp_diastolic", 90, "mmHg", 12),
            _vital(john.id, "bp_diastolic", 86, "mmHg", 10),
            _vital(john.id, "bp_diastolic", 89, "mmHg", 8),
            _vital(john.id, "bp_diastolic", 84, "mmHg", 7),
            _vital(john.id, "bp_diastolic", 87, "mmHg", 5),
            _vital(john.id, "bp_diastolic", 85, "mmHg", 4),
            _vital(john.id, "bp_diastolic", 88, "mmHg", 2),
            _vital(john.id, "bp_diastolic", 90, "mmHg", 1),
            _vital(john.id, "bp_diastolic", 86, "mmHg", 0),
            # weight: 3 readings, stable ~91 kg
            _vital(john.id, "weight", 91.2, "kg", 12, source="manual"),
            _vital(john.id, "weight", 90.8, "kg", 5, source="manual"),
            _vital(john.id, "weight", 91.0, "kg", 0, source="manual"),
            # sleep_hours: 8 readings, 6-8h range
            _vital(john.id, "sleep_hours", 7.0, "h", 13),
            _vital(john.id, "sleep_hours", 6.5, "h", 11),
            _vital(john.id, "sleep_hours", 8.0, "h", 9),
            _vital(john.id, "sleep_hours", 7.5, "h", 7),
            _vital(john.id, "sleep_hours", 6.0, "h", 5),
            _vital(john.id, "sleep_hours", 7.0, "h", 3),
            _vital(john.id, "sleep_hours", 7.5, "h", 1),
            _vital(john.id, "sleep_hours", 6.5, "h", 0),

            # ── Peter Novak (medium risk: heart failure) ──────────────────
            # bp_systolic: 8 readings, 120-135 range
            _vital(peter.id, "bp_systolic", 128, "mmHg", 13),
            _vital(peter.id, "bp_systolic", 132, "mmHg", 11),
            _vital(peter.id, "bp_systolic", 125, "mmHg", 9),
            _vital(peter.id, "bp_systolic", 130, "mmHg", 7),
            _vital(peter.id, "bp_systolic", 127, "mmHg", 5),
            _vital(peter.id, "bp_systolic", 135, "mmHg", 3),
            _vital(peter.id, "bp_systolic", 122, "mmHg", 1),
            _vital(peter.id, "bp_systolic", 129, "mmHg", 0),
            # bp_diastolic: 8 readings, 75-85
            _vital(peter.id, "bp_diastolic", 79, "mmHg", 13),
            _vital(peter.id, "bp_diastolic", 82, "mmHg", 11),
            _vital(peter.id, "bp_diastolic", 76, "mmHg", 9),
            _vital(peter.id, "bp_diastolic", 80, "mmHg", 7),
            _vital(peter.id, "bp_diastolic", 78, "mmHg", 5),
            _vital(peter.id, "bp_diastolic", 85, "mmHg", 3),
            _vital(peter.id, "bp_diastolic", 75, "mmHg", 1),
            _vital(peter.id, "bp_diastolic", 81, "mmHg", 0),
            # weight: daily for 14 days, creeping up 82.5 → 84.5 (fluid retention)
            _vital(peter.id, "weight", 82.5, "kg", 13, source="manual"),
            _vital(peter.id, "weight", 82.6, "kg", 12, source="manual"),
            _vital(peter.id, "weight", 82.7, "kg", 11, source="manual"),
            _vital(peter.id, "weight", 82.8, "kg", 10, source="manual"),
            _vital(peter.id, "weight", 83.0, "kg", 9, source="manual"),
            _vital(peter.id, "weight", 82.9, "kg", 8, source="manual"),
            _vital(peter.id, "weight", 83.1, "kg", 7, source="manual"),
            _vital(peter.id, "weight", 83.3, "kg", 6, source="manual"),
            _vital(peter.id, "weight", 83.5, "kg", 5, source="manual"),
            _vital(peter.id, "weight", 83.7, "kg", 4, source="manual"),
            _vital(peter.id, "weight", 83.9, "kg", 3, source="manual"),
            _vital(peter.id, "weight", 84.0, "kg", 2, source="manual"),
            _vital(peter.id, "weight", 84.3, "kg", 1, source="manual"),
            _vital(peter.id, "weight", 84.5, "kg", 0, source="manual"),
            # sleep_hours: 10 readings, 5-7h
            _vital(peter.id, "sleep_hours", 5.5, "h", 13),
            _vital(peter.id, "sleep_hours", 6.0, "h", 11),
            _vital(peter.id, "sleep_hours", 5.0, "h", 10),
            _vital(peter.id, "sleep_hours", 6.5, "h", 8),
            _vital(peter.id, "sleep_hours", 5.5, "h", 7),
            _vital(peter.id, "sleep_hours", 7.0, "h", 5),
            _vital(peter.id, "sleep_hours", 6.0, "h", 4),
            _vital(peter.id, "sleep_hours", 5.5, "h", 2),
            _vital(peter.id, "sleep_hours", 6.5, "h", 1),
            _vital(peter.id, "sleep_hours", 6.0, "h", 0),

            # ── Amina Diallo (low risk: recovery) ─────────────────────────
            # sleep_hours: 10 readings, improving 5.5 → 8h
            _vital(amina.id, "sleep_hours", 5.5, "h", 13),
            _vital(amina.id, "sleep_hours", 5.0, "h", 12),
            _vital(amina.id, "sleep_hours", 6.0, "h", 10),
            _vital(amina.id, "sleep_hours", 6.5, "h", 9),
            _vital(amina.id, "sleep_hours", 6.0, "h", 7),
            _vital(amina.id, "sleep_hours", 7.0, "h", 5),
            _vital(amina.id, "sleep_hours", 7.5, "h", 4),
            _vital(amina.id, "sleep_hours", 7.0, "h", 2),
            _vital(amina.id, "sleep_hours", 8.0, "h", 1),
            _vital(amina.id, "sleep_hours", 7.5, "h", 0),
            # weight: 4 readings, stable ~63 kg
            _vital(amina.id, "weight", 63.1, "kg", 12, source="manual"),
            _vital(amina.id, "weight", 62.8, "kg", 8, source="manual"),
            _vital(amina.id, "weight", 63.3, "kg", 4, source="manual"),
            _vital(amina.id, "weight", 63.0, "kg", 0, source="manual"),

            # ── Lena Fischer (low risk: post-op) ──────────────────────────
            # sleep_hours: 7 readings, 6-8h
            _vital(lena.id, "sleep_hours", 6.5, "h", 13),
            _vital(lena.id, "sleep_hours", 7.0, "h", 10),
            _vital(lena.id, "sleep_hours", 6.0, "h", 8),
            _vital(lena.id, "sleep_hours", 7.5, "h", 6),
            _vital(lena.id, "sleep_hours", 8.0, "h", 4),
            _vital(lena.id, "sleep_hours", 7.0, "h", 2),
            _vital(lena.id, "sleep_hours", 7.5, "h", 0),
            # weight: 3 readings, stable ~66 kg
            _vital(lena.id, "weight", 66.2, "kg", 11, source="manual"),
            _vital(lena.id, "weight", 65.8, "kg", 5, source="manual"),
            _vital(lena.id, "weight", 66.0, "kg", 0, source="manual"),
        ])

        db.add_all([
            models.Medication(patient_id=maria.id, name="Lisinopril", dose="10mg",
                              schedule="once daily", is_new_prescription=True,
                              started_at=today - timedelta(days=5)),
            models.Medication(patient_id=maria.id, name="Metformin", dose="500mg",
                              schedule="twice daily", is_new_prescription=False),
            models.Medication(patient_id=john.id, name="Amlodipine", dose="5mg",
                              schedule="once daily", is_new_prescription=False),
            models.Medication(patient_id=peter.id, name="Furosemide", dose="40mg",
                              schedule="once daily in the morning", is_new_prescription=False),
            models.Medication(patient_id=peter.id, name="Apixaban", dose="5mg",
                              schedule="twice daily", is_new_prescription=False),
            models.Medication(patient_id=amina.id, name="Ibuprofen", dose="400mg",
                              schedule="as needed, max 3x daily", is_new_prescription=False),
            models.Medication(patient_id=lena.id, name="Paracetamol", dose="500mg",
                              schedule="as needed", is_new_prescription=True,
                              started_at=today - timedelta(days=2)),
        ])

        maria_call = _prior_call(
            db, maria, days_ago=7,
            summary="BP high (158/96). Reports occasional dizziness and a mild cough.",
            structured={"bp_systolic": 158, "bp_diastolic": 96, "symptoms": ["cough", "dizziness"],
                        "meds_taken": True, "missing_items": []},
            risk_flags=["high_bp"], proposed_risk="high",
        )
        john_call = _prior_call(
            db, john, days_ago=5,
            summary="BP 138/88, taking Amlodipine daily, no complaints.",
            structured={"bp_systolic": 138, "bp_diastolic": 88, "meds_taken": True,
                        "symptoms": [], "missing_items": []},
            risk_flags=[],
        )
        peter_call = _prior_call(
            db, peter, days_ago=6,
            summary="Feels well; mild ankle swelling in the evenings. Did not weigh himself.",
            structured={"symptoms": ["ankle swelling"], "meds_taken": True,
                        "missing_items": ["weight"]},
            risk_flags=["edema"],
        )
        db.flush()
        # One open draft decision so the triage queue is not empty in the demo.
        db.add(models.TriageDecision(
            call_session_id=maria_call.id, patient_id=maria.id, urgency="monitor",
            recommended_action="request_appointment",
            rationale="BP above target after starting Lisinopril; dizziness reported.",
            source="pipeline", rule_hits=[], llm_urgency="monitor",
            uncertain_because=["single BP reading"], doctor_reviewed=False,
            created_at=now - timedelta(days=7),
        ))

        db.add_all([
            models.Appointment(patient_id=maria.id, doctor_id=doc.id,
                               scheduled_at=now + timedelta(days=5), type="follow-up",
                               notes="Post-op review"),
            models.Appointment(patient_id=john.id, doctor_id=doc.id,
                               scheduled_at=now + timedelta(days=12), type="check-up",
                               notes="Quarterly BP review"),
            models.Appointment(patient_id=lena.id, doctor_id=doc.id,
                               scheduled_at=now + timedelta(days=3), type="wound check",
                               notes="Suture removal"),
        ])
        db.add(models.QuestionTemplate(doctor_id=doc.id, patient_id=maria.id,
                                       questions=list(MARIA_QUESTIONS)))
        db.commit()
        result = {
            "doctor_id": doc.id,
            "patient_ids": {p.name: p.id for p in (maria, john, amina, peter, lena)},
            "prior_session_ids": [maria_call.id, john_call.id, peter_call.id],
        }
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        if own:
            db.close()


if __name__ == "__main__":
    reset_schema()
    ids = seed()
    print(f"Seeded fictional demo data: 1 doctor, {len(ids['patient_ids'])} patients, "
          f"vitals/meds/prior reports/appointments/question templates.")
