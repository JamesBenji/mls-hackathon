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
            # Maria: BP trending down but still high, sugar elevated.
            _vital(maria.id, "bp_systolic", 158, "mmHg", 7),
            _vital(maria.id, "bp_diastolic", 96, "mmHg", 7),
            _vital(maria.id, "bp_systolic", 146, "mmHg", 1),
            _vital(maria.id, "bp_diastolic", 91, "mmHg", 1),
            _vital(maria.id, "blood_sugar", 180, "mg/dL", 1),
            _vital(maria.id, "weight", 78, "kg", 3, source="manual"),
            _vital(maria.id, "sleep_hours", 5.5, "h", 1),
            # John: steady.
            _vital(john.id, "bp_systolic", 138, "mmHg", 5),
            _vital(john.id, "bp_diastolic", 88, "mmHg", 5),
            _vital(john.id, "weight", 91, "kg", 5, source="manual"),
            # Peter: weight creeping up (fluid?) - something for the doctor to see.
            _vital(peter.id, "weight", 83.1, "kg", 6, source="manual"),
            _vital(peter.id, "weight", 84.0, "kg", 2, source="manual"),
            _vital(peter.id, "bp_systolic", 128, "mmHg", 2),
            _vital(peter.id, "bp_diastolic", 79, "mmHg", 2),
            # Amina: one manual reading.
            _vital(amina.id, "sleep_hours", 7.5, "h", 2, source="manual"),
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
