"""Doctor-reviewed follow-up plans. All timestamps in storage are naive UTC."""
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import DateTime, ForeignKey, String, Text, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from app import models, timeutil
from app.adapters import gemma_client
from app.config import settings
from app.database import Base, JSONType, StrList, get_db
from app.services import scheduler
from app.tracking import TrackedItem, normalize_items, observations_out


class FollowUpPlan(Base):
    __tablename__ = "follow_up_plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"))
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"))
    instructions: Mapped[str] = mapped_column(Text)
    context: Mapped[str] = mapped_column(Text, default="")
    questions: Mapped[list] = mapped_column(StrList, default=list)
    tracked_items: Mapped[list] = mapped_column(JSONType, default=list)
    revision: Mapped[int] = mapped_column(default=1)
    photo_policy: Mapped[str] = mapped_column(String(16), default="none")
    schedule: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    next_call_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    legacy_job_id: Mapped[int | None] = mapped_column(ForeignKey("call_jobs.id"), nullable=True)


class PlanCall(Base):
    """Additive session linkage and hashed per-session photo-tool credential."""
    __tablename__ = "follow_up_plan_calls"
    session_id: Mapped[int] = mapped_column(ForeignKey("call_sessions.id"), primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("follow_up_plans.id"), index=True)
    credential_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    photo_request_key: Mapped[str] = mapped_column(String(64))
    photo_allowed: Mapped[bool] = mapped_column(default=False)
    approved_context: Mapped[dict] = mapped_column(JSONType, default=dict)


class Schedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timezone: str
    local_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    frequency: Literal["once", "daily"]
    start_date: date
    end_date: date

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Use an IANA timezone") from None
        return value

    @model_validator(mode="after")
    def bounds(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date precedes start_date")
        if (self.end_date - self.start_date).days > 366:
            raise ValueError("Review and renew plans after at most 366 days")
        if self.frequency == "once" and self.end_date != self.start_date:
            raise ValueError("once requires matching start_date and end_date")
        return self


def next_occurrence(schedule: dict, after: datetime) -> datetime | None:
    spec = Schedule.model_validate(schedule)
    zone = ZoneInfo(spec.timezone)
    day = max(spec.start_date, after.replace(tzinfo=UTC).astimezone(zone).date())
    while day <= spec.end_date:
        if spec.frequency == "once" and day != spec.start_date:
            return None
        local = datetime.combine(day, time.fromisoformat(spec.local_time), zone)
        utc = local.astimezone(UTC)
        # Skip nonexistent DST wall times; ambiguous times use the first occurrence.
        if utc.astimezone(zone).replace(tzinfo=None) == local.replace(tzinfo=None):
            naive = utc.replace(tzinfo=None)
            if naive > after:
                return naive
        day += timedelta(days=1)
    return None


class DraftIn(BaseModel):
    doctor_id: int
    patient_id: int
    instructions: str = Field(min_length=1, max_length=5000)


class DraftOutput(BaseModel):
    context: str = ""
    questions: list[str] = Field(default_factory=list, max_length=30)
    tracked_items: list[TrackedItem | str] = Field(default_factory=list, max_length=30)
    photo_policy: Literal["none", "first_call", "every_call"] = "none"
    clarification: str | None = None


class PlanPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instructions: str | None = Field(default=None, min_length=1, max_length=5000)
    context: str | None = Field(default=None, max_length=10000)
    questions: list[str] | None = Field(default=None, max_length=30)
    tracked_items: list[TrackedItem | str] | None = Field(default=None, max_length=30)
    photo_policy: Literal["none", "first_call", "every_call"] | None = None
    schedule: Schedule | None = None


def owned_patient(db, doctor_id, patient_id):
    patient = db.get(models.Patient, patient_id)
    if patient is None or patient.doctor_id != doctor_id:
        raise HTTPException(404, "Patient not found for this doctor")
    return patient


def get_plan(db, plan_id):
    plan = db.scalar(select(FollowUpPlan).where(FollowUpPlan.id == plan_id).with_for_update())
    if plan is None:
        raise HTTPException(404, "Plan not found")
    owned_patient(db, plan.doctor_id, plan.patient_id)
    return plan


def plan_out(db, plan, *, detail=False):
    calls = db.scalars(select(models.CallSession).join(
        PlanCall, PlanCall.session_id == models.CallSession.id
    ).where(PlanCall.plan_id == plan.id).order_by(models.CallSession.id.desc()).limit(10)).all()
    results = []
    for call in calls:
        report = db.scalar(select(models.CallReport).where(
            models.CallReport.call_session_id == call.id
        ).order_by(models.CallReport.id.desc()))
        results.append({"id": call.id, "call_session_id": call.id, "status": call.status,
                        "started_at": call.started_at, "summary": report.summary if report else None,
                        "results": report.structured_data if report else None})
    out = {key: getattr(plan, key) for key in (
        "id", "doctor_id", "patient_id", "instructions", "context", "questions",
        "tracked_items", "photo_policy", "schedule", "status", "next_call_at", "revision")}
    out["patient_name"] = db.get(models.Patient, plan.patient_id).name
    out["last_summary"] = next((r["summary"] for r in results if r["summary"]), None)
    if detail:
        out["calls"] = results
        out["observations"] = observations_out(db, plan.id)
    return out


router = APIRouter(prefix="/follow-up-plans", tags=["follow-up-plans"])
Db = Annotated[Session, Depends(get_db)]


@router.get("")
def list_plans(db: Db, doctor_id: int | None = None):
    stmt = select(FollowUpPlan).order_by(FollowUpPlan.id.desc())
    if doctor_id is not None:
        stmt = stmt.where(FollowUpPlan.doctor_id == doctor_id)
    return [plan_out(db, p) for p in db.scalars(stmt)]


@router.post("/draft")
async def draft_plan(body: DraftIn, db: Db):
    owned_patient(db, body.doctor_id, body.patient_id)
    if not body.instructions.strip():
        raise HTTPException(422, "Please provide doctor instructions")
    draft = await interpret_instructions(body.instructions)
    plan = FollowUpPlan(doctor_id=body.doctor_id, patient_id=body.patient_id,
                        instructions=body.instructions, **draft)
    db.add(plan)
    db.commit()
    return plan_out(db, plan)


async def interpret_instructions(instructions, previous=()):
    import json
    try:
        draft = await gemma_client.chat_json(settings.gemma_model, [
            {"role": "system", "content": "Draft a small follow-up interview from doctor instructions. "
             "Do not diagnose, invent clinical thresholds, prescribe treatment, or schedule calls. "
             "Only include context, questions, tracking and photo permission explicitly supported by "
             "the instructions. Derive typed tracked_items definitions from natural language; the doctor "
             "never writes a schema. Use IDs stable against previous definitions when only labels change. "
             "Use new IDs for type/unit/scale/meaning changes. Never invent clinical units, scales, options "
             "or numeric bounds. min/max are explicitly specified scale bounds, NOT clinical alert thresholds. "
             "Ask clarification if ambiguous units/scales are clinically consequential. Default photo_policy "
             "to none. Return a readable interpretation in context for doctor review, not schema instructions. "
             "This is an unapproved draft for explicit doctor review."},
            {"role": "user", "content": json.dumps({"instructions": instructions,
                                                        "previous_definitions": list(previous)})},
        ], DraftOutput)
    except gemma_client.ModelError:
        raise HTTPException(503, "Draft generation unavailable; try again later") from None
    if draft.clarification or not draft.questions:
        raise HTTPException(422, draft.clarification or "What questions should the patient answer?")
    output = draft.model_dump(exclude={"clarification"})
    try:
        output["tracked_items"] = normalize_items(output["tracked_items"], previous)
    except ValueError:
        raise HTTPException(422, "Please clarify the tracking definitions, units and scales") from None
    return output


@router.get("/{plan_id}")
def detail(plan_id: int, db: Db):
    return plan_out(db, get_plan(db, plan_id), detail=True)


def stop_pending(db, plan):
    scheduler.cancel(f"plan:{plan.id}")
    plan.next_call_at = None
    db.execute(update(PlanCall).where(PlanCall.plan_id == plan.id).values(credential_hash=None))
    for call in db.scalars(select(models.CallSession).join(
        PlanCall, PlanCall.session_id == models.CallSession.id
    ).where(PlanCall.plan_id == plan.id)):
        scheduler.cancel(f"resume:{call.id}")
        if call.status in ("scheduled", "missed", "callback_pending"):
            call.status = "cancelled"
            call.callback_at = None


@router.patch("/{plan_id}")
async def edit(plan_id: int, body: PlanPatch, db: Db):
    plan = get_plan(db, plan_id)
    if plan.status == "finished":
        raise HTTPException(409, "Finished plans cannot be edited; create a new draft")
    changes = body.model_dump(mode="json", exclude_unset=True)
    if any(value is None for value in changes.values()):
        raise HTTPException(422, "Fields cannot be null; omit unchanged fields")
    if "instructions" in changes and changes["instructions"] != plan.instructions:
        # Instruction editing is the natural-language regeneration flow; no schema editor needed.
        before = (plan.revision, plan.status)
        previous = plan.tracked_items
        db.commit()  # Never hold a plan lock during model generation.
        interpreted = await interpret_instructions(changes["instructions"], previous)
        db.refresh(plan)
        if (plan.revision, plan.status) != before:
            raise HTTPException(409, "Plan changed during drafting; reload and review again")
        changes.update(interpreted)
    if "tracked_items" in changes:
        try:
            changes["tracked_items"] = normalize_items(changes["tracked_items"], plan.tracked_items)
        except ValueError:
            raise HTTPException(422, "Invalid tracking definitions") from None
    if not any(getattr(plan, key) != value for key, value in changes.items()):
        return plan_out(db, plan)
    stop_pending(db, plan)
    for key, value in changes.items():
        setattr(plan, key, value)
    plan.status = "draft"  # Any edit invalidates approval.
    plan.revision += 1
    db.commit()
    return plan_out(db, plan)


@router.post("/{plan_id}/activate")
def activate(plan_id: int, db: Db):
    plan = get_plan(db, plan_id)
    if plan.status == "finished":
        raise HTTPException(409, "Finished plans cannot be activated")
    if not plan.instructions.strip() or not plan.questions or any(not q.strip() for q in plan.questions):
        raise HTTPException(422, "Review instructions and nonempty questions before activation")
    if not plan.schedule:
        raise HTTPException(422, "Review and set a schedule before activation")
    try:
        at = next_occurrence(plan.schedule, timeutil.utcnow())
    except ValueError:
        raise HTTPException(422, "Invalid schedule") from None
    if at is None:
        raise HTTPException(422, "Schedule has no future occurrence")
    plan.status, plan.next_call_at = "active", at
    db.commit()
    schedule_plan(plan)
    return plan_out(db, plan)


@router.post("/{plan_id}/pause")
def pause(plan_id: int, db: Db):
    plan = get_plan(db, plan_id)
    if plan.status == "finished":
        raise HTTPException(409, "Plan is finished")
    stop_pending(db, plan)
    plan.status = "paused"
    db.commit()
    return plan_out(db, plan)


@router.post("/{plan_id}/finish")
def finish(plan_id: int, db: Db):
    plan = get_plan(db, plan_id)
    stop_pending(db, plan)
    plan.status = "finished"
    db.commit()
    return plan_out(db, plan)


@router.post("/{plan_id}/call")
async def call_now(plan_id: int, db: Db):
    plan = get_plan(db, plan_id)
    if plan.status != "active":
        raise HTTPException(409, "Activate the reviewed plan before calling")
    from app.services.call_orchestrator import CallStartError, start_call
    try:
        await start_call(db, plan.patient_id, follow_up_plan_id=plan.id)
    except CallStartError:
        raise HTTPException(502, "Call could not be placed") from None
    return plan_out(db, plan, detail=True)


def schedule_plan(plan):
    if plan.status == "active" and plan.next_call_at:
        scheduler._add_date_job(f"plan:{plan.id}", "app.followups:run_plan", [plan.id], plan.next_call_at)


async def run_plan(plan_id):
    from app.database import SessionLocal
    from app.services.call_orchestrator import start_call
    with SessionLocal() as db:
        plan = get_plan(db, plan_id)
        now = timeutil.utcnow()
        if plan.status != "active" or not plan.next_call_at or plan.next_call_at > now:
            return
        # Atomic due-slot claim. Restarts/duplicate jobs cannot replay a consumed slot.
        due = plan.next_call_at
        at = next_occurrence(plan.schedule, max(now, due))
        claimed = db.execute(update(FollowUpPlan).where(
            FollowUpPlan.id == plan.id, FollowUpPlan.status == "active",
            FollowUpPlan.next_call_at == due).values(next_call_at=at)).rowcount
        db.commit()
        if not claimed:
            return
        try:
            # Never catch up a backlog of daily calls after downtime.
            if now - due < timedelta(hours=1):
                await start_call(db, plan.patient_id, follow_up_plan_id=plan.id)
        except Exception:
            import logging
            logging.getLogger(__name__).warning("Scheduled plan call failed for plan %s", plan_id)
        finally:
            db.refresh(plan)
            schedule_plan(plan)


def reload_plans(db):
    for plan in db.scalars(select(FollowUpPlan).where(FollowUpPlan.status == "active")):
        schedule_plan(plan)
