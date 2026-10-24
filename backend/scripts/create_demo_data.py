"""Add idempotent, explicitly synthetic demo data; never schedule or send anything."""
import json
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from uuid import uuid4

# The deployment entrypoint exports DATABASE_URL only to its own process tree.
if not os.environ.get("DATABASE_URL") and os.environ.get("POSTGRES_PASSWORD"):
    password = quote(os.environ["POSTGRES_PASSWORD"], safe="")
    os.environ["DATABASE_URL"] = f"postgresql://guardian:{password}@postgres:5432/guardian"

from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.models import CallReport, CallSession, Doctor, Patient
from app.followups import FollowUpPlan, PlanCall
from app.photos.models import PhotoRequest
from app.tracking import Observation, normalize_items


def main():
    # Additive only; no seed/reset, scheduler, API calls, push, or credentials.
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with SessionLocal.begin() as db:
        doctor = db.scalar(select(Doctor).where(Doctor.email == "medicall-operator@example.invalid"))
        if doctor is None:
            doctor = Doctor(name="Medicall Care Team", email="medicall-operator@example.invalid")
            db.add(doctor)
            db.flush()
        patient = db.scalar(select(Patient).where(
            Patient.doctor_id == doctor.id, Patient.name == "Alex Morgan", Patient.phone == "NOT_DIALABLE"))
        if patient is None:
            patient = Patient(doctor_id=doctor.id, name="Alex Morgan", phone="NOT_DIALABLE",
                              category="Synthetic demo", chronic_conditions=[], risk_level="low")
            db.add(patient)
            db.flush()
        specs = [
            ("wound", "Synthetic demo: wound recovery follow-up",
             "Ask Alex about wound recovery each day. Record pain from 0 (no pain) to 10 "
             "(worst pain), and whether redness is none, mild, or spreading. Ask about dressing "
             "changes and request a clear wound photo. This is synthetic demonstration data; "
             "the plan is paused and must not place calls.",
             [{"id": "demo-wound-pain", "label": "Wound pain (0-10)", "type": "number", "min": 0, "max": 10},
              {"id": "demo-wound-redness", "label": "Wound redness", "type": "choice", "options": ["none", "mild", "spreading"]}],
             [[6, "mild"], [5, "mild"], [3, "mild"], [2, "none"]], "every_call"),
            ("mobility", "Synthetic demo: mobility and sleep follow-up",
             "Ask how many minutes Alex walked today and how many hours they slept last night. "
             "Track walking minutes and sleep hours separately from wound recovery. "
             "Synthetic demonstration only; keep paused with no scheduled calls.",
             [{"id": "demo-walk-minutes", "label": "Walking duration", "type": "number", "unit": "min", "min": 0, "max": 1440},
              {"id": "demo-sleep-hours", "label": "Sleep duration", "type": "number", "unit": "hours", "min": 0, "max": 24}],
             [[10, 5.5], [15, 6], [20, 7], [25, 7.5]], "none"),
        ]
        plan_ids = []
        for key, context, instructions, raw_items, days, photo_policy in specs:
            plan = db.scalar(select(FollowUpPlan).where(
                FollowUpPlan.doctor_id == doctor.id, FollowUpPlan.patient_id == patient.id,
                FollowUpPlan.context == context))
            if plan is None:
                plan = FollowUpPlan(doctor_id=doctor.id, patient_id=patient.id, instructions=instructions,
                                    context=context, questions=[item["label"] for item in raw_items],
                                    tracked_items=normalize_items(raw_items), revision=1,
                                    photo_policy=photo_policy, status="paused", next_call_at=None, schedule=None)
                db.add(plan)
                db.flush()
            plan_ids.append(plan.id)
            for index, values in enumerate(days):
                marker = f"synthetic-demo-{key}-day-{index + 1}"
                existing = db.scalar(select(PlanCall).where(
                    PlanCall.plan_id == plan.id, PlanCall.photo_request_key == marker))
                if existing is not None:
                    continue
                at = now - timedelta(days=3 - index, hours=1)
                session = CallSession(patient_id=patient.id, direction="outbound", channel="demo",
                                      status="completed", started_at=at, ended_at=at + timedelta(minutes=2),
                                      attempt=0, checkpoint={"synthetic": True, "note": "No call was placed."})
                db.add(session)
                db.flush()
                db.add(PlanCall(session_id=session.id, plan_id=plan.id, photo_request_key=marker,
                                photo_allowed=False, approved_context={"revision": plan.revision,
                                    "tracked_items": plan.tracked_items, "synthetic": True}))
                summary = "SYNTHETIC DEMO - no patient was contacted. " + "; ".join(
                    f"{item['label']}: {value}" for item, value in zip(plan.tracked_items, values))
                db.add(CallReport(call_session_id=session.id, patient_id=patient.id, summary=summary,
                                  model_info={"source": "synthetic_demo", "no_call_placed": True}))
                for item, value in zip(plan.tracked_items, values):
                    db.add(Observation(plan_id=plan.id, item_id=item["id"], plan_revision=plan.revision,
                                       definition_snapshot=item, call_session_id=session.id, value=value,
                                       status="reported", unit=item["unit"],
                                       source_quote=f"SYNTHETIC DEMO (not collected): {item['label']} = {value}",
                                       reported_at=at, measured_at=None))
        request = db.scalar(select(PhotoRequest).where(
            PhotoRequest.doctor_id == doctor.id, PhotoRequest.idempotency_key == "synthetic-demo-wound-photo-v1"))
        if request is None:
            request = PhotoRequest(id=str(uuid4()), patient_id=patient.id, doctor_id=doctor.id,
                idempotency_key="synthetic-demo-wound-photo-v1",
                instructions="Synthetic demo: take a clear, well-lit wound photo. Keep the area centered, "
                             "avoid identifying details, and review the image before uploading.",
                status="pending", push_status="not_sent", push_attempts=0)
            db.add(request)
        result = {"doctor_id": doctor.id, "patient_id": patient.id, "plan_ids": plan_ids,
                  "photo_request_id": request.id, "synthetic": True,
                  "plans_paused": True, "push_sent": False, "calls_placed": 0}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
