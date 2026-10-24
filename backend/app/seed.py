"""Create tables and seed demo data.

Run from the backend/ dir:  python -m app.seed
Idempotent-ish: drops and recreates all tables, then inserts demo rows.
"""
from datetime import date, datetime, timedelta

from app.database import Base, SessionLocal, engine
from app import models


def reset_schema() -> None:
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def seed() -> None:
    db = SessionLocal()
    try:
        doc = models.Doctor(name="Dr. Lee", email="lee@example.com")
        db.add(doc)
        db.flush()

        # --- Patient 1: post-op, hypertensive, diabetic, high risk ---
        maria = models.Patient(
            doctor_id=doc.id, name="Maria Gomez", phone="+15550000001",
            dob=date(1958, 4, 12), blood_type="O+", height_cm=162, weight_kg=78,
            chronic_conditions=["hypertension", "type 2 diabetes"],
            category="post-op", risk_level="high",
        )
        # --- Patient 2: chronic, moderate ---
        john = models.Patient(
            doctor_id=doc.id, name="John Carter", phone="+15550000002",
            dob=date(1971, 9, 3), blood_type="A-", height_cm=178, weight_kg=91,
            chronic_conditions=["hypertension"], category="chronic", risk_level="medium",
        )
        # --- Patient 3: recovery, low ---
        amina = models.Patient(
            doctor_id=doc.id, name="Amina Diallo", phone="+15550000003",
            dob=date(1990, 1, 22), blood_type="B+", height_cm=167, weight_kg=63,
            chronic_conditions=[], category="recovery", risk_level="low",
        )
        db.add_all([maria, john, amina])
        db.flush()

        # Vitals history for Maria (shows a trend the agent can reference)
        now = datetime.utcnow()
        db.add_all([
            models.Vital(patient_id=maria.id, type="bp_systolic", value=158,
                         unit="mmHg", source="call", recorded_at=now - timedelta(days=7)),
            models.Vital(patient_id=maria.id, type="bp_systolic", value=146,
                         unit="mmHg", source="call", recorded_at=now - timedelta(days=1)),
            models.Vital(patient_id=maria.id, type="blood_sugar", value=180,
                         unit="mg/dL", source="call", recorded_at=now - timedelta(days=1)),
            models.Vital(patient_id=maria.id, type="weight", value=78,
                         unit="kg", source="manual", recorded_at=now - timedelta(days=3)),
        ])

        # Medications
        db.add_all([
            models.Medication(patient_id=maria.id, name="Lisinopril", dose="10mg",
                              schedule="once daily", is_new_prescription=True,
                              started_at=date.today() - timedelta(days=5)),
            models.Medication(patient_id=maria.id, name="Metformin", dose="500mg",
                              schedule="twice daily", is_new_prescription=False),
        ])

        # A prior call report => becomes agent memory for the next call
        cs = models.CallSession(patient_id=maria.id, direction="outbound",
                                channel="voice", status="completed",
                                started_at=now - timedelta(days=7),
                                ended_at=now - timedelta(days=7))
        db.add(cs)
        db.flush()
        db.add(models.CallReport(
            call_session_id=cs.id, patient_id=maria.id,
            summary="BP high (158). Reports occasional dizziness. Mild cough mentioned.",
            structured_data={"bp_systolic": 158, "dizziness": True, "cough": "mild"},
            risk_flags=["high_bp"], escalated=False,
        ))

        # Upcoming in-person appointment
        db.add(models.Appointment(
            patient_id=maria.id, doctor_id=doc.id,
            scheduled_at=now + timedelta(days=5), type="follow-up",
            notes="Post-op review",
        ))

        # Doctor's default question template
        db.add(models.QuestionTemplate(
            doctor_id=doc.id, patient_id=maria.id,
            questions=[
                "Did you take your blood pressure today?",
                "Did you check your blood sugar? What was the reading?",
                "Did you take your medication today, and at what time?",
                "Any side effects since starting the new medication?",
                "How many hours did you sleep last night?",
                "How are you feeling overall today?",
            ],
        ))

        db.commit()
        print("Seeded: 1 doctor, 3 patients, vitals/meds/report/appointment/questions.")
    finally:
        db.close()


if __name__ == "__main__":
    reset_schema()
    seed()
