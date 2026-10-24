"""Patients, vitals, reports, triage and /sms/send routes (shapes the
dashboard relies on + validation)."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app import models, timeutil

LIST_FIELDS = {"id", "name", "phone", "category", "risk_level", "chronic_conditions"}
DETAIL_FIELDS = {"id", "name", "phone", "blood_type", "height_cm", "weight_kg",
                 "chronic_conditions", "category", "risk_level", "vitals", "medications",
                 "reports", "appointments", "proposed_risk_level"}
TRIAGE_FIELDS = {"id", "patient_id", "call_session_id", "urgency", "recommended_action",
                 "rationale", "source", "rule_hits", "uncertain_because", "created_at"}


# --- patients ------------------------------------------------------------------------

def test_list_patients_and_filters(client, seeded):
    rows = client.get("/patients").json()
    assert [r["name"] for r in rows] == ["Maria Gomez", "John Carter"]
    assert LIST_FIELDS <= set(rows[0])
    assert rows[0]["chronic_conditions"] == ["hypertension", "type 2 diabetes"]
    assert [r["name"] for r in client.get("/patients?category=post-op").json()] == ["Maria Gomez"]
    assert [r["name"] for r in client.get("/patients?risk_level=medium").json()] == ["John Carter"]
    assert client.get("/patients?risk_level=extreme").status_code == 422


def test_patient_detail_shape(client, db, seeded):
    mid = seeded["maria_id"]
    db.add(models.Appointment(patient_id=mid, doctor_id=seeded["doctor_id"],
                              scheduled_at=timeutil.utcnow() + timedelta(days=2),
                              type="follow-up"))
    db.add(models.Appointment(patient_id=mid, doctor_id=seeded["doctor_id"],
                              scheduled_at=timeutil.utcnow() - timedelta(days=2),
                              type="past visit"))
    db.commit()
    p = client.get(f"/patients/{mid}").json()
    assert DETAIL_FIELDS <= set(p)
    assert (p["blood_type"], p["height_cm"], p["risk_level"]) == ("O+", 162, "high")
    assert {"type", "value", "unit", "recorded_at"} <= set(p["vitals"][0])
    assert [v["type"] for v in p["vitals"]] == ["bp_systolic", "bp_diastolic", "blood_sugar"]
    lisinopril = next(m for m in p["medications"] if m["name"] == "Lisinopril")
    assert {"name", "dose", "schedule", "is_new_prescription"} <= set(lisinopril)
    assert lisinopril["is_new_prescription"] is True
    [report] = p["reports"]
    assert {"id", "summary", "escalated", "created_at", "status"} <= set(report)
    assert report["status"] == "ok" and "cough" in report["summary"].lower()
    assert [a["type"] for a in p["appointments"]] == ["follow-up"]  # upcoming only


def test_patient_detail_proposed_risk_and_404(client, db, seeded):
    db.add(models.CallReport(call_session_id=seeded["prior_session_id"],
                             patient_id=seeded["maria_id"], status="partial",
                             summary="newer", proposed_risk_level="medium",
                             created_at=timeutil.utcnow()))
    db.commit()
    p = client.get(f"/patients/{seeded['maria_id']}").json()
    assert p["proposed_risk_level"] == "medium"
    assert p["risk_level"] == "high"  # never auto-applied
    assert [r["summary"] for r in p["reports"]][0] == "newer"
    assert client.get("/patients/9999").status_code == 404


def test_patch_patient(client, db, seeded):
    mid = seeded["maria_id"]
    r = client.patch(f"/patients/{mid}", json={"risk_level": "medium", "weight_kg": 76.5,
                                               "phone": "+1 (555) 000-0099",
                                               "chronic_conditions": ["hypertension", " "]})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == mid
    assert body["updated"] == {"risk_level": "medium", "weight_kg": 76.5,
                               "phone": "+15550000099", "chronic_conditions": ["hypertension"]}
    db.expire_all()
    p = db.get(models.Patient, mid)
    assert (p.risk_level, p.weight_kg, p.phone) == ("medium", 76.5, "+15550000099")


def test_patch_validation(client, seeded):
    mid = seeded["maria_id"]
    assert client.patch(f"/patients/{mid}", json={"risk_level": "critical"}).status_code == 422
    assert client.patch(f"/patients/{mid}", json={"phone": "015112345678"}).status_code == 422
    assert client.patch(f"/patients/{mid}", json={"risk_level": None}).status_code == 422
    assert client.patch(f"/patients/{mid}", json={"height_cm": -5}).status_code == 422
    # John's number is taken.
    assert client.patch(f"/patients/{mid}", json={"phone": "+15550000002"}).status_code == 409
    assert client.patch("/patients/9999", json={"risk_level": "low"}).status_code == 404


# --- vitals / reports --------------------------------------------------------------------

def test_add_and_list_vitals(client, seeded):
    mid = seeded["maria_id"]
    r = client.post(f"/patients/{mid}/vitals", json={"type": "bp_systolic", "value": 141})
    assert r.status_code == 200
    v = r.json()
    assert (v["type"], v["value"], v["unit"], v["source"]) == ("bp_systolic", 141, "mmHg", "manual")
    r = client.post(f"/patients/{mid}/vitals", json={
        "type": "weight", "value": 77, "unit": "kg", "recorded_at": "2020-01-01T10:00:00+01:00"})
    assert r.json()["recorded_at"] == "2020-01-01T09:00:00"  # stored as naive UTC

    rows = client.get(f"/patients/{mid}/vitals").json()
    assert rows[0]["recorded_at"] == "2020-01-01T09:00:00"  # oldest first
    assert rows[-1]["value"] == 141
    only_bp = client.get(f"/patients/{mid}/vitals?type=bp_systolic").json()
    assert {r["type"] for r in only_bp} == {"bp_systolic"} and len(only_bp) == 2


def test_vitals_validation_and_404(client, seeded):
    mid = seeded["maria_id"]
    assert client.post(f"/patients/{mid}/vitals",
                       json={"type": "heart_vibes", "value": 1}).status_code == 422
    assert client.post(f"/patients/{mid}/vitals",
                       json={"type": "weight", "value": -1}).status_code == 422
    assert client.post("/patients/9999/vitals",
                       json={"type": "weight", "value": 70}).status_code == 404
    assert client.get("/patients/9999/vitals").status_code == 404


def test_reports_list(client, seeded):
    rows = client.get(f"/patients/{seeded['maria_id']}/reports").json()
    [r] = rows
    assert r["status"] == "ok" and r["risk_flags"] == ["high_bp"]
    assert r["structured_data"]["bp_systolic"] == 158
    assert r["call_session_id"] == seeded["prior_session_id"]
    assert client.get(f"/patients/{seeded['john_id']}/reports").json() == []
    assert client.get("/patients/9999/reports").status_code == 404


# --- triage --------------------------------------------------------------------------

def _triage(db, seeded, **kw) -> models.TriageDecision:
    t = models.TriageDecision(call_session_id=seeded["prior_session_id"],
                              patient_id=seeded["maria_id"], urgency="urgent",
                              recommended_action="doctor_callback", rationale="r",
                              rule_hits=["chest_pain: chest pain"], **kw)
    db.add(t)
    db.commit()
    return t


def test_triage_queue_newest_first_unreviewed_only(client, db, seeded):
    now = timeutil.utcnow()
    old = _triage(db, seeded, created_at=now - timedelta(hours=2))
    new = _triage(db, seeded, created_at=now, source="sms")
    _triage(db, seeded, created_at=now, doctor_reviewed=True)
    rows = client.get("/triage").json()
    assert [r["id"] for r in rows] == [new.id, old.id]
    assert TRIAGE_FIELDS <= set(rows[0])
    assert rows[0]["source"] == "sms" and rows[0]["rule_hits"] == ["chest_pain: chest pain"]
    assert rows[0]["patient_name"] == "Maria Gomez"


def test_triage_review_override(client, db, seeded):
    t = _triage(db, seeded)
    r = client.post(f"/triage/{t.id}/review",
                    json={"confirmed": True, "override_action": "ambulance"})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == t.id and body["doctor_reviewed"] is True
    assert body["recommended_action"] == "ambulance"
    db.expire_all()
    row = db.get(models.TriageDecision, t.id)
    assert row.doctor_reviewed is True and row.reviewed_at is not None
    assert client.get("/triage").json() == []


def test_triage_review_confirm_and_validation(client, db, seeded):
    t = _triage(db, seeded)
    r = client.post(f"/triage/{t.id}/review", json={})
    assert r.json()["recommended_action"] == "doctor_callback"
    assert client.post(f"/triage/{t.id}/review",
                       json={"override_action": "helicopter"}).status_code == 422
    assert client.post("/triage/9999/review", json={}).status_code == 404


# --- sms/send --------------------------------------------------------------------------

def test_sms_send_to_patient(client, db, seeded):
    r = client.post("/sms/send", json={"patient_id": seeded["maria_id"],
                                       "body": "Remember your BP reading.", "kind": "reminder"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"id", "status", "provider_sid", "error"}
    assert body["status"] == "mock"
    row = db.scalars(select(models.SmsLog).where(models.SmsLog.id == body["id"])).one()
    assert (row.to_number, row.kind, row.patient_id) == (
        "+15550000001", "reminder", seeded["maria_id"])


def test_sms_send_validation(client, seeded):
    assert client.post("/sms/send", json={"body": "hi"}).status_code == 422
    assert client.post("/sms/send", json={"patient_id": 9999, "body": "hi"}).status_code == 404
    assert client.post("/sms/send", json={"to": "12345", "body": "hi"}).status_code == 422
    assert client.post("/sms/send", json={"to": "+15550000001", "body": ""}).status_code == 422
    assert client.post("/sms/send", json={"to": "+15550000001", "body": "hi",
                                          "kind": "spam"}).status_code == 422
    r = client.post("/sms/send", json={"to": "+1 555 000 0007", "body": "tip"})
    assert r.status_code == 200 and r.json()["status"] == "mock"
