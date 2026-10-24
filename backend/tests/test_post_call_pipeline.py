"""handle_post_call pipeline + /calls/{id}/analyze + /clinical routes.

The clinical module is monkeypatched (FakeClinical) so these tests pin the
orchestration: persistence order, partial reports, vitals write-back, the
risk proposal never being applied, triage rows as doctor drafts.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app import models
from app.schemas.clinical import CallReportDraft, CapturedData, Soap, TriageResult
from app.services import call_orchestrator, clinical
from tests.test_webhooks import count, make_session

TRANSCRIPT = ("Agent: Did you take your blood pressure today?\n"
              "Patient: Yes, 150 over 92. Sugar was 140, I weigh 77.5 and slept 6.5 hours.")


class FakeClinical:
    def __init__(self) -> None:
        self.report_calls: list[dict] = []
        self.report_error: Exception | None = None
        self.triage_error: Exception | None = None
        self.urgency = "urgent"
        self.proposed = "high"

    async def generate_report_detailed(self, transcript, patient, last_reports):
        self.report_calls.append({"transcript": transcript, "patient": patient,
                                  "reports": last_reports})
        if self.report_error is not None:
            raise self.report_error
        draft = CallReportDraft(
            summary="BP 150/92, sugar 140. No new symptoms.",
            soap=Soap(subjective="Feels ok.", objective="BP 150/92 (self-reported).",
                      assessment="BP above target.", plan="Doctor to review."),
            captured=CapturedData(bp_systolic=150, bp_diastolic=92, blood_sugar=140,
                                  weight_kg=77.5, sleep_hours=6.5, meds_taken=True),
            risk_flags=["high_bp"], changes_since_last_call=["BP slightly lower"])
        return clinical.GeneratedReport(draft=draft, clinical_analysis="stage-1 notes",
                                        model_info={"pipeline": "two_stage"})

    async def triage_detailed(self, report, transcript, patient):
        if self.triage_error is not None:
            raise self.triage_error
        return clinical.TriageOutcome(
            result=TriageResult(urgency=self.urgency, recommended_action="doctor_callback",
                                rationale="BP above target", uncertain_because=[]),
            rule_hits=["bp_crisis: 150/92"] if self.urgency == "urgent" else [],
            llm_urgency="monitor")

    def propose_risk_level(self, patient, report, triage_result):
        return self.proposed


@pytest.fixture
def fake_clinical(monkeypatch) -> FakeClinical:
    fake = FakeClinical()
    for name in ("generate_report_detailed", "triage_detailed", "propose_risk_level"):
        monkeypatch.setattr(clinical, name, getattr(fake, name))
    return fake


def add_transcript(db, sid: int, patient_id: int | None, text: str = TRANSCRIPT,
                   conv: str | None = "conv-1") -> None:
    db.add(models.Transcription(source="call", text=text, call_session_id=sid,
                                patient_id=patient_id, conversation_id=conv))
    db.commit()


async def test_report_vitals_triage_and_risk_proposal(db, seeded, fake_clinical):
    john = seeded["john_id"]
    sid = make_session(db, john, status="completed")
    add_transcript(db, sid, john)
    await call_orchestrator.handle_post_call(sid)
    db.expire_all()

    report = db.scalars(select(models.CallReport)
                        .where(models.CallReport.call_session_id == sid)).one()
    assert report.status == "ok" and report.error is None
    assert report.summary.startswith("BP 150/92")
    assert report.soap_note == ("S: Feels ok.\nO: BP 150/92 (self-reported).\n"
                                "A: BP above target.\nP: Doctor to review.")
    assert report.soap["assessment"] == "BP above target."
    assert report.structured_data["bp_systolic"] == 150
    assert report.risk_flags == ["high_bp"]
    assert report.changes_since_last_call == ["BP slightly lower"]
    assert report.clinical_analysis == "stage-1 notes"
    assert report.model_info == {"pipeline": "two_stage"}
    assert report.escalated is True
    # Risk level is only a proposal; the patient's level is untouched.
    assert report.proposed_risk_level == "high"
    assert db.get(models.Patient, john).risk_level == "medium"

    tri = db.scalars(select(models.TriageDecision)).one()
    assert (tri.source, tri.urgency, tri.recommended_action) == ("pipeline", "urgent",
                                                                 "doctor_callback")
    assert tri.doctor_reviewed is False and tri.llm_urgency == "monitor"
    assert tri.rule_hits == ["bp_crisis: 150/92"]

    vitals = {v.type: (v.value, v.unit, v.source, v.call_session_id)
              for v in db.scalars(select(models.Vital)
                                  .where(models.Vital.patient_id == john)).all()}
    assert vitals == {
        "bp_systolic": (150, "mmHg", "call", sid), "bp_diastolic": (92, "mmHg", "call", sid),
        "blood_sugar": (140, "mg/dL", "call", sid), "weight": (77.5, "kg", "call", sid),
        "sleep_hours": (6.5, "h", "call", sid),
    }


async def test_model_failure_gives_partial_report_and_keeps_transcript(db, seeded,
                                                                       fake_clinical):
    fake_clinical.report_error = RuntimeError("ollama unreachable")
    maria = seeded["maria_id"]
    sid = make_session(db, maria, status="completed")
    add_transcript(db, sid, maria)
    await call_orchestrator.handle_post_call(sid)
    db.expire_all()
    report = db.scalars(select(models.CallReport)
                        .where(models.CallReport.call_session_id == sid)).one()
    assert report.status == "partial"
    assert "report generation failed: RuntimeError" in report.error
    assert report.summary == "[report generation failed: ollama unreachable] Transcript saved."
    assert count(db, models.Transcription, models.Transcription.call_session_id == sid) == 1
    # Triage still runs on the transcript alone.
    assert db.scalars(select(models.TriageDecision)).one().doctor_reviewed is False
    assert db.get(models.Patient, maria).risk_level == "high"


async def test_triage_failure_falls_back_to_doctor_review(db, seeded, fake_clinical):
    fake_clinical.triage_error = RuntimeError("bug")
    sid = make_session(db, seeded["john_id"], status="completed")
    add_transcript(db, sid, seeded["john_id"])
    await call_orchestrator.handle_post_call(sid)
    db.expire_all()
    tri = db.scalars(select(models.TriageDecision)).one()
    assert tri.recommended_action == "doctor_callback"
    assert tri.urgency in ("monitor", "urgent") and tri.doctor_reviewed is False
    report = db.scalars(select(models.CallReport)
                        .where(models.CallReport.call_session_id == sid)).one()
    assert report.status == "partial" and "triage failed" in report.error


async def test_history_excludes_own_report_and_ok_report_is_not_rebuilt(db, seeded,
                                                                         fake_clinical):
    maria = seeded["maria_id"]
    sid = make_session(db, maria, status="completed")
    add_transcript(db, sid, maria)
    await call_orchestrator.handle_post_call(sid)
    await call_orchestrator.handle_post_call(sid)  # duplicate job -> skipped
    assert len(fake_clinical.report_calls) == 1
    await call_orchestrator.handle_post_call(sid, force=True)
    assert len(fake_clinical.report_calls) == 2
    own = {r.id for r in db.scalars(select(models.CallReport)
                                    .where(models.CallReport.call_session_id == sid))}
    assert not own & {r.id for r in fake_clinical.report_calls[1]["reports"]}
    assert fake_clinical.report_calls[1]["reports"][0].summary.startswith("BP high")
    # Re-analysis replaces this session's vitals instead of duplicating them.
    assert count(db, models.Vital, models.Vital.call_session_id == sid) == 5


async def test_unknown_caller_notifies_doctor(db, seeded, fake_clinical, monkeypatch):
    notified: list[dict] = []

    async def fake_notify(**kwargs):
        notified.append(kwargs)

    from app.services import notifications
    monkeypatch.setattr(notifications, "notify_doctor", fake_notify)
    sid = make_session(db, None, status="in_progress")
    add_transcript(db, sid, None, "Agent: How can I help?\nPatient: This is Ana, I need a refill.")
    await call_orchestrator.handle_post_call(sid)
    db.expire_all()
    assert db.get(models.CallSession, sid).status == "completed"
    assert notified and "Ana" in notified[0]["body"]
    assert fake_clinical.report_calls == []


def test_calls_analyze_route_runs_the_pipeline(client, db, seeded, fake_clinical):
    sid = make_session(db, seeded["john_id"], status="completed")
    r = client.post(f"/calls/{sid}/analyze", json={"transcript": TRANSCRIPT})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["transcripts"][0]["source"] == "manual"
    assert body["report"]["status"] == "ok"
    assert body["report"]["captured"]["bp_systolic"] == 150
    assert body["report"]["proposed_risk_level"] == "high"
    assert body["triage"][0]["doctor_reviewed"] is False
    assert client.post("/calls/9999/analyze", json={"transcript": "x"}).status_code == 404


def test_calls_callback_route(client, db, seeded):
    sid = make_session(db, seeded["maria_id"])
    r = client.post(f"/calls/{sid}/callback", json={"minutes": "2", "missing_item": "weight"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["callback_at"] and len(body["callback_time"]) == 5
    db.expire_all()
    assert db.get(models.CallSession, sid).status == "callback_pending"


def test_clinical_analyze_route(client, db, seeded, fake_clinical):
    john = seeded["john_id"]
    r = client.post(f"/clinical/{john}/analyze", json={"transcript": TRANSCRIPT})
    assert r.status_code == 200
    body = r.json()
    assert body["saved"] is None
    assert body["analysis"]["report"]["captured"]["bp_systolic"] == 150
    assert body["analysis"]["triage"]["urgency"] == "urgent"
    assert count(db, models.CallReport, models.CallReport.patient_id == john) == 0

    sid = make_session(db, john, status="completed")
    r = client.post(f"/clinical/{john}/analyze",
                    json={"transcript": TRANSCRIPT, "call_session_id": sid})
    saved = r.json()["saved"]
    assert saved["report_id"] and saved["triage_id"]
    assert client.post("/clinical/9999/analyze", json={"transcript": "x"}).status_code == 404


def test_clinical_question_plan_route(client, seeded, monkeypatch):
    from app.schemas.clinical import PlannedQuestion, QuestionPlan

    async def plan(patient, reports, questions):
        return QuestionPlan(questions=[
            PlannedQuestion(id="q1", text=questions[0], source="doctor"),
            PlannedQuestion(id="r1", text=clinical.DEFAULT_REDFLAG_PROBE,
                            source="redflag_probe")])

    monkeypatch.setattr(clinical, "build_question_plan", plan)
    r = client.get(f"/clinical/{seeded['maria_id']}/question-plan")
    assert r.status_code == 200
    body = r.json()
    assert body["question_plan"][0] == "Did you take your blood pressure today?"
    assert body["plan"]["questions"][1]["source"] == "redflag_probe"
    assert body["rendered"]
    assert client.get("/clinical/9999/question-plan").status_code == 404


async def test_fallback_triage_uses_real_redflag_rules(db, seeded, fake_clinical):
    """Integration with the real redflags module: a chest-pain complaint is
    never under-triaged even when the triage step itself crashes."""
    fake_clinical.triage_error = RuntimeError("bug")
    sid = make_session(db, seeded["john_id"], status="completed")
    add_transcript(db, sid, seeded["john_id"],
                   "Agent: How are you today?\nPatient: I have chest pain since this morning.")
    await call_orchestrator.handle_post_call(sid)
    db.expire_all()
    tri = db.scalars(select(models.TriageDecision)).one()
    assert tri.urgency == "urgent" and tri.rule_hits
    assert db.scalars(select(models.CallReport).where(
        models.CallReport.call_session_id == sid)).one().escalated is True
