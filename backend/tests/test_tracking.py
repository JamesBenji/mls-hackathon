"""Minimal versioning and patient-report extraction checks; no live model calls."""
import pytest
from sqlalchemy import select

from app import models, timeutil
from app.adapters import gemma_client
from app.followups import FollowUpPlan, PlanCall
from app.tracking import (
    ExtractedObservation,
    Extraction,
    Observation,
    extract_observations,
    normalize_items,
    validated_value,
)


def definitions():
    return normalize_items([
        {"id": "score", "label": "Pain on the doctor's 0–10 scale", "type": "number", "min": 0, "max": 10},
        {"id": "taken", "label": "Medication taken", "type": "boolean"},
        {"id": "note", "label": "Additional patient comments", "type": "text"},
    ])


def test_definition_ids_and_validation():
    old = definitions()
    relabel = [{**old[0], "label": "Reported pain score"}]
    assert normalize_items(relabel, old)[0]["id"] == "score"
    changed = normalize_items([{**old[0], "max": 5}], old)[0]
    assert changed["id"] != "score"
    assert old[0]["max"] == 10
    assert normalize_items(["Legacy note"])[0]["type"] == "text"
    for value in (11, -1, True, "4"):
        with pytest.raises(ValueError):
            validated_value(old[0], ExtractedObservation(item_id="score", value=value, status="reported"))
    with pytest.raises(ValueError):
        validated_value(old[0], ExtractedObservation(item_id="score", value=4, unit="invented", status="reported"))


@pytest.mark.asyncio
async def test_observations_preserve_approved_revision(db, seeded, monkeypatch, client):
    items = definitions()
    plan = FollowUpPlan(doctor_id=seeded["doctor_id"], patient_id=seeded["maria_id"],
                        instructions="Ask about pain on a 0–10 scale, medication and comments.",
                        questions=["How is your pain?"], tracked_items=items, status="active")
    db.add(plan)
    db.flush()
    call = models.CallSession(patient_id=plan.patient_id, direction="outbound", status="completed",
                              ended_at=timeutil.utcnow())
    db.add(call)
    db.flush()
    db.add(PlanCall(session_id=call.id, plan_id=plan.id, photo_request_key="offline-key",
                    approved_context={"tracked_items": items, "revision": 1}))
    db.commit()
    async def extract(*args, **kwargs):
        return Extraction(observations=[
            ExtractedObservation(item_id="score", value=4, status="reported", source_quote="My pain is 4"),
            ExtractedObservation(item_id="taken", value=False, status="reported", source_quote="I did not take it"),
            ExtractedObservation(item_id="unknown-item", value=99, status="reported", source_quote="My pain is 4"),
        ])
    monkeypatch.setattr(gemma_client, "chat_json", extract)
    transcript = "Agent: How is your pain?\nPatient: My pain is 4. I did not take it."
    await extract_observations(db, call.id, transcript)
    await extract_observations(db, call.id, transcript)  # Duplicate postcall delivery.
    rows = db.scalars(select(Observation).order_by(Observation.id)).all()
    assert len(rows) == 3
    assert [(r.item_id, r.value, r.status) for r in rows] == [
        ("score", 4, "reported"), ("taken", False, "reported"), ("note", None, "missing")]
    assert all(r.measured_at is None and r.plan_revision == 1 for r in rows)
    response = client.patch(f"/follow-up-plans/{plan.id}", json={
        "tracked_items": [{**items[0], "max": 5}, *items[1:]]})
    assert response.status_code == 200
    updated = response.json()
    assert updated["revision"] == 2 and updated["tracked_items"][0]["id"] != "score"
    detail = client.get(f"/follow-up-plans/{plan.id}").json()
    original = detail["observations"][0]
    assert original["plan_revision"] == 1 and original["item_id"] == "score"
    assert original["definition_snapshot"]["max"] == 10
