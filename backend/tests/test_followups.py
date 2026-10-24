"""Essential approval, bounded scheduling and session-scoped photo checks (offline)."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app import timeutil
from app.adapters import elevenlabs, gemma_client
from app.config import settings
from app.followups import DraftOutput, FollowUpPlan, PlanCall, Schedule, next_occurrence, run_plan
from app.main import app  # noqa: F401 -- register all additive tables before fixtures
from app.photos.models import PhotoRequest
from app.services import scheduler


def draft(client, seeded, monkeypatch):
    async def generate(*args, **kwargs):
        return DraftOutput(context="Post-procedure follow-up; existing dressing instructions unchanged",
                           questions=["How is the dressing?"], tracked_items=["dressing"],
                           photo_policy="first_call")
    monkeypatch.setattr(gemma_client, "chat_json", generate)
    response = client.post("/follow-up-plans/draft", json={
        "doctor_id": seeded["doctor_id"], "patient_id": seeded["maria_id"],
        "instructions": "Ask about the dressing and request a photo on the first call."})
    assert response.status_code == 200, response.text
    return response.json()


def schedule():
    tomorrow = (timeutil.utcnow() + timedelta(days=1)).date().isoformat()
    return {"timezone": "Europe/Berlin", "local_time": "10:30", "frequency": "once",
            "start_date": tomorrow, "end_date": tomorrow}


def test_review_required_and_pause_cancels(client, db, seeded, monkeypatch):
    plan = draft(client, seeded, monkeypatch)
    pid = plan["id"]
    assert plan["status"] == "draft" and plan["next_call_at"] is None
    assert scheduler.get_job(f"plan:{pid}") is None
    assert not db.scalars(select(PlanCall)).all()
    assert client.post(f"/follow-up-plans/{pid}/call").status_code == 422
    assert client.post(f"/follow-up-plans/{pid}/activate").status_code == 422
    assert client.patch(f"/follow-up-plans/{pid}", json={"schedule": schedule()}).status_code == 200
    assert client.post(f"/follow-up-plans/{pid}/activate").json()["status"] == "active"
    assert scheduler.get_job(f"plan:{pid}") is not None
    assert client.post(f"/follow-up-plans/{pid}/pause").json()["next_call_at"] is None
    assert scheduler.get_job(f"plan:{pid}") is None
    assert client.post(f"/follow-up-plans/{pid}/call").status_code == 422
    assert client.post(f"/follow-up-plans/{pid}/finish").json()["status"] == "finished"
    assert client.post(f"/follow-up-plans/{pid}/activate").status_code == 409


def test_auth_ownership_and_clarification(client, seeded, monkeypatch):
    monkeypatch.setattr(settings, "guardian_api_key", "offline-test-key")
    assert client.get("/follow-up-plans").status_code == 401
    headers = {"X-API-Key": "offline-test-key"}
    payload = {"doctor_id": seeded["doctor_id"] + 100, "patient_id": seeded["maria_id"],
               "instructions": "Ask about recovery"}
    assert client.post("/follow-up-plans/draft", json=payload, headers=headers).status_code == 404
    async def unclear(*args, **kwargs):
        return DraftOutput(clarification="What should we ask?")
    monkeypatch.setattr(gemma_client, "chat_json", unclear)
    payload["doctor_id"] = seeded["doctor_id"]
    response = client.post("/follow-up-plans/draft", json=payload, headers=headers)
    assert response.status_code == 422 and response.json()["detail"] == "What should we ask?"


def test_plan_context_and_photo_authorization(client, db, seeded, monkeypatch):
    plan = draft(client, seeded, monkeypatch)
    pid = plan["id"]
    client.patch(f"/follow-up-plans/{pid}", json={"schedule": schedule()})
    client.post(f"/follow-up-plans/{pid}/activate")
    captured = []
    async def place(_number, variables, **kwargs):
        captured.append(variables)
        return {"conversation_id": f"offline-conv-{len(captured)}", "call_sid": "offline", "mock": True}
    monkeypatch.setattr(elevenlabs, "place_outbound_call", place)
    reviewed = client.get(f"/follow-up-plans/{pid}").json()
    from app.models import Patient
    result = client.post(f"/follow-up-plans/{pid}/call", json={
        "approved": True, "revision": reviewed["revision"],
        "expected_phone": db.get(Patient, seeded["maria_id"]).phone,
        "request_key": "offline-approval-0001", "max_duration_seconds": 60,
    })
    assert result.status_code == 200, result.text
    variables = captured[0]
    assert "existing dressing instructions" in variables["question_plan"]
    assert "blood pressure" not in variables["question_plan"]  # No generic question-bank leakage.
    assert all(k in variables for k in elevenlabs.REQUIRED_DYNAMIC_VARIABLES)
    body = {"session_id": variables["session_id"], "conversation_id": "offline-conv-1",
            "instructions": "Show the dressing in good light"}
    headers = {"Authorization": variables["secret__photo_tool_authorization"],
               "Idempotency-Key": variables["photo_request_key"]}
    assert client.post("/tools/request_photo", json=body,
                       headers={"Idempotency-Key": variables["photo_request_key"]}).status_code == 401
    wrong = {**body, "conversation_id": "other-conversation"}
    assert client.post("/tools/request_photo", json=wrong, headers=headers).status_code == 403
    response = client.post("/tools/request_photo", json=body, headers=headers)
    assert response.status_code == 200, response.text
    photo = response.json()
    assert photo["status"] == "waiting_for_upload"
    assert client.post("/tools/request_photo", json=body, headers=headers).json() == photo
    assert len(db.scalars(select(PhotoRequest)).all()) == 1
    status_body = {k: body[k] for k in ("session_id", "conversation_id")}
    status_body["request_id"] = photo["request_id"]
    assert client.post("/tools/check_photo_status", json=status_body, headers=headers).status_code == 200
    client.post(f"/follow-up-plans/{pid}/pause")
    assert client.post("/tools/request_photo", json=body, headers=headers).status_code == 401


def test_paused_one_off_is_idempotent_and_never_schedules(client, db, seeded, monkeypatch):
    from app.models import Patient

    plan = draft(client, seeded, monkeypatch)
    pid = plan["id"]
    client.post(f"/follow-up-plans/{pid}/pause")
    calls = []

    async def place(number, variables, **options):
        calls.append((variables, options))
        return {"conversation_id": "one-off-conversation", "call_sid": "offline", "mock": True}

    monkeypatch.setattr(elevenlabs, "place_outbound_call", place)
    approval = {
        "approved": True, "revision": plan["revision"],
        "expected_phone": db.get(Patient, seeded["maria_id"]).phone,
        "request_key": "one-off-approval-0001", "max_duration_seconds": 60,
    }
    url = f"/follow-up-plans/{pid}/call"
    assert client.post(url, json={**approval, "revision": 999}).status_code == 409
    assert client.post(url, json={**approval, "max_duration_seconds": 3601}).status_code == 422
    response = client.post(url, json=approval)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "paused" and result["next_call_at"] is None
    assert result["call_dispatch"]["mock"] is True
    assert calls[0][1]["max_duration_seconds"] == 60
    assert client.post(url, json=approval).status_code == 200
    assert len(calls) == 1
    assert scheduler.get_job(f"plan:{pid}") is None
    assert client.post(url, json={**approval, "request_key": "another-approval-0002"}).status_code == 409

    variables = calls[0][0]
    response = client.post("/tools/request_photo", json={
        "session_id": variables["session_id"], "conversation_id": "one-off-conversation",
        "instructions": "Show the dressing in good light",
    }, headers={
        "Authorization": variables["secret__photo_tool_authorization"],
        "Idempotency-Key": variables["photo_request_key"],
    })
    assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_quality_uses_images_and_validated_technical_schema(monkeypatch):
    from app.photos.quality import QualityResult, assess_photo_quality
    async def generate(model, messages, schema_model, **kwargs):
        assert kwargs["images_b64"] == ["encoded-image"]
        assert schema_model is QualityResult
        return QualityResult(status="retake_recommended", issues=["glare"])
    monkeypatch.setattr(gemma_client, "chat_json", generate)
    result = await assess_photo_quality(["encoded-image"])
    assert result.issues == ["glare"]
    with pytest.raises(ValueError):
        QualityResult(status="clear_enough", issues=["diagnosis"])


@pytest.mark.asyncio
async def test_bounded_daily_execution_and_stale_queue(db, seeded, monkeypatch):
    now = datetime(2026, 10, 24, 12)
    monkeypatch.setattr(timeutil, "utcnow", lambda: now)
    spec = {"timezone": "UTC", "local_time": "12:00", "frequency": "daily",
            "start_date": "2026-10-24", "end_date": "2026-10-25"}
    assert next_occurrence(spec, now) == datetime(2026, 10, 25, 12)
    assert next_occurrence(spec, datetime(2026, 10, 25, 12)) is None
    with pytest.raises(ValueError):
        Schedule.model_validate({**spec, "local_time": "25:00"})
    with pytest.raises(ValueError):
        Schedule.model_validate({**spec, "timezone": "Not/AZone"})
    plan = FollowUpPlan(doctor_id=seeded["doctor_id"], patient_id=seeded["maria_id"],
                        instructions="Ask about recovery", questions=["How are you?"],
                        status="paused", schedule=spec, next_call_at=now)
    db.add(plan)
    db.commit()
    await run_plan(plan.id)
    assert not db.scalars(select(PlanCall)).all()
