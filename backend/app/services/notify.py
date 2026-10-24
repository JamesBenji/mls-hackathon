"""Notification orchestrator: report -> PDF -> doctor email.

Called after a call report is created (by the telephony layer's post-call
handler, or directly via the API). Builds the PDF, stores its path on the
report, and emails the doctor a summary + dashboard link + PDF attachment.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.services import email as email_service
from app.services import pdf as pdf_service


def _report_dict(r: models.CallReport) -> dict:
    return {
        "id": r.id, "summary": r.summary, "soap_note": r.soap_note,
        "structured_data": r.structured_data, "risk_flags": r.risk_flags,
        "escalated": r.escalated,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _patient_dict(p: models.Patient) -> dict:
    return {
        "name": p.name, "phone": p.phone,
        "dob": p.dob.isoformat() if p.dob else None,
        "blood_type": p.blood_type, "category": p.category,
        "risk_level": p.risk_level, "chronic_conditions": p.chronic_conditions,
        "height_cm": p.height_cm, "weight_kg": p.weight_kg,
    }


def _session_dict(s: models.CallSession | None) -> dict | None:
    if not s:
        return None
    duration = None
    if s.started_at and s.ended_at:
        secs = int((s.ended_at - s.started_at).total_seconds())
        duration = f"{secs // 60}m {secs % 60}s"
    return {
        "direction": s.direction, "channel": s.channel, "status": s.status,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "ended_at": s.ended_at.isoformat() if s.ended_at else None,
        "duration": duration,
    }


def _email_html(patient: models.Patient, report: models.CallReport,
                triage: models.TriageDecision | None, doctor: models.Doctor) -> str:
    link = f"{settings.public_base_url}/patients/{patient.id}"
    urgency = (triage.urgency if triage else "none").upper()
    urgency_color = {"EMERGENCY": "#c0392b", "URGENT": "#e67e22",
                     "MONITOR": "#2980b9", "NONE": "#27ae60"}.get(urgency, "#333")
    return f"""
    <div style="font-family:sans-serif;max-width:600px;margin:auto">
      <div style="background:#1B7F79;padding:20px;border-radius:6px 6px 0 0">
        <h1 style="color:#fff;margin:0;font-size:22px">Guardian — Call Report</h1>
        <p style="color:#cef;margin:4px 0 0">Dr. {doctor.name}</p>
      </div>
      <div style="background:#f9f9f9;padding:20px;border:1px solid #ddd;border-top:none">
        <p>A check-in call with <b>{patient.name}</b> has been completed.</p>
        <table style="border-collapse:collapse;width:100%;margin-bottom:16px">
          <tr>
            <td style="padding:6px 12px;background:#e8f3f2;font-weight:bold;width:140px">Triage urgency</td>
            <td style="padding:6px 12px;color:{urgency_color};font-weight:bold">{urgency}</td>
          </tr>
          {"<tr><td style='padding:6px 12px;background:#e8f3f2;font-weight:bold'>Action</td><td style='padding:6px 12px'>" + triage.recommended_action + "</td></tr>" if triage else ""}
        </table>
        <p><b>Summary:</b><br>{report.summary or "—"}</p>
        {"<p style='color:#c0392b'><b>⚠ Escalated — immediate review required.</b></p>" if report.escalated else ""}
        <p><a href="{link}" style="background:#1B7F79;color:#fff;padding:10px 20px;text-decoration:none;border-radius:4px;display:inline-block">Open patient dashboard</a></p>
        <p style="color:#888;font-size:12px">Full clinical report PDF is attached.</p>
      </div>
    </div>
    """


async def notify_doctor(db: Session, report_id: int) -> dict:
    """Generate the PDF and email the patient's doctor. Returns a status dict."""
    report = db.get(models.CallReport, report_id)
    if not report:
        raise ValueError(f"report {report_id} not found")
    patient = db.get(models.Patient, report.patient_id)
    doctor = db.get(models.Doctor, patient.doctor_id)

    triage = db.scalars(
        select(models.TriageDecision).where(
            models.TriageDecision.call_session_id == report.call_session_id
        )
    ).first()

    session = db.get(models.CallSession, report.call_session_id)

    vitals = db.scalars(
        select(models.Vital)
        .where(models.Vital.patient_id == patient.id)
        .order_by(models.Vital.recorded_at.desc())
        .limit(20)
    ).all()

    medications = db.scalars(
        select(models.Medication).where(models.Medication.patient_id == patient.id)
    ).all()

    # 1. PDF
    pdf_path = pdf_service.generate_report_pdf(
        report=_report_dict(report),
        patient=_patient_dict(patient),
        triage={"urgency": triage.urgency,
                "recommended_action": triage.recommended_action,
                "rationale": triage.rationale} if triage else None,
        session=_session_dict(session),
        vitals=[{"type": v.type, "value": v.value, "unit": v.unit,
                 "recorded_at": v.recorded_at.isoformat() if v.recorded_at else None,
                 "source": v.source} for v in vitals],
        medications=[{"name": m.name, "dose": m.dose, "schedule": m.schedule,
                      "is_new": m.is_new_prescription} for m in medications],
        doctor_name=doctor.name,
    )
    report.pdf_path = pdf_path
    db.commit()

    # 2. Email
    result = await email_service.send_email(
        to=doctor.email,
        subject=f"Guardian: call with {patient.name} completed",
        html=_email_html(patient, report, triage, doctor),
        pdf_path=pdf_path,
    )
    return {"report_id": report.id, "pdf_path": pdf_path, "email": result}
