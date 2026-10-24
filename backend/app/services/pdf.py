"""PDF report generation for a completed call report (reportlab)."""
from __future__ import annotations

import os
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

REPORTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "reports_out")

TEAL = colors.HexColor("#1B7F79")
TEAL_LIGHT = colors.HexColor("#E8F3F2")
RED = colors.HexColor("#c0392b")
ORANGE = colors.HexColor("#e67e22")
BLUE = colors.HexColor("#2980b9")
GREY = colors.HexColor("#7f8c8d")
DARK = colors.HexColor("#2c3e50")

URGENCY_COLOR = {
    "emergency": RED, "urgent": ORANGE,
    "monitor": BLUE, "none": colors.HexColor("#27ae60"),
}


def _styles():
    s = getSampleStyleSheet()
    s.add(ParagraphStyle("GTitle", parent=s["Normal"], fontSize=22, textColor=colors.white,
                         fontName="Helvetica-Bold", spaceAfter=2))
    s.add(ParagraphStyle("GSubtitle", parent=s["Normal"], fontSize=11, textColor=TEAL_LIGHT,
                         fontName="Helvetica"))
    s.add(ParagraphStyle("GSectionHead", parent=s["Normal"], fontSize=12, textColor=TEAL,
                         fontName="Helvetica-Bold", spaceBefore=8, spaceAfter=4))
    s.add(ParagraphStyle("GLabel", parent=s["Normal"], fontSize=9, textColor=GREY))
    s.add(ParagraphStyle("GBody", parent=s["Normal"], fontSize=10, textColor=DARK,
                         leading=14))
    s.add(ParagraphStyle("GAlert", parent=s["Normal"], fontSize=10, textColor=RED,
                         fontName="Helvetica-Bold"))
    return s


def _section(flow, title, s):
    flow.append(Spacer(1, 4 * mm))
    flow.append(HRFlowable(width="100%", thickness=1, color=TEAL_LIGHT))
    flow.append(Paragraph(title, s["GSectionHead"]))


def _kv_table(rows, col_widths=(45 * mm, 110 * mm)):
    t = Table(rows, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), GREY),
        ("TEXTCOLOR", (1, 0), (1, -1), DARK),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, TEAL_LIGHT]),
    ]))
    return t


def generate_report_pdf(
    report: dict,
    patient: dict,
    triage: dict | None = None,
    session: dict | None = None,
    vitals: list[dict] | None = None,
    medications: list[dict] | None = None,
    doctor_name: str | None = None,
) -> str:
    """Render a full clinical report to PDF; return the file path."""
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.abspath(
        os.path.join(REPORTS_DIR, f"report_{report.get('id', 'x')}.pdf")
    )
    doc = SimpleDocTemplate(
        path, pagesize=A4, title="Guardian Call Report",
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=14 * mm, bottomMargin=14 * mm,
    )
    s = _styles()
    flow = []

    # ── Header banner ────────────────────────────────────────────────────────
    header_data = [[
        Paragraph("Guardian — Clinical Call Report", s["GTitle"]),
        Paragraph(f"Generated {datetime.now():%Y-%m-%d %H:%M}", s["GLabel"]),
    ]]
    header_table = Table(header_data, colWidths=[120 * mm, 55 * mm])
    header_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), TEAL),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    flow.append(header_table)
    flow.append(Spacer(1, 4 * mm))

    # ── Triage alert ─────────────────────────────────────────────────────────
    if triage:
        urgency = triage.get("urgency", "none").lower()
        urg_color = URGENCY_COLOR.get(urgency, GREY)
        urg_label = urgency.upper()
        alert_data = [[
            Paragraph(f"TRIAGE: {urg_label}", ParagraphStyle(
                "UrgencyLabel", parent=s["GBody"], fontSize=13,
                fontName="Helvetica-Bold", textColor=colors.white)),
            Paragraph(
                f"Action: {triage.get('recommended_action', '—')}",
                ParagraphStyle("UrgencyAction", parent=s["GBody"],
                               textColor=colors.white, fontSize=10)),
        ]]
        alert_table = Table(alert_data, colWidths=[60 * mm, 115 * mm])
        alert_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), urg_color),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        flow.append(alert_table)
        if triage.get("rationale"):
            flow.append(Spacer(1, 2 * mm))
            flow.append(Paragraph(f"Rationale: {triage['rationale']}", s["GBody"]))
        flow.append(Spacer(1, 3 * mm))

    if report.get("escalated"):
        flow.append(Paragraph("⚠  ESCALATED — Immediate doctor review required.", s["GAlert"]))
        flow.append(Spacer(1, 2 * mm))

    # ── Patient information ───────────────────────────────────────────────────
    _section(flow, "Patient Information", s)
    rows = [["Patient", patient.get("name", "—")]]
    if patient.get("dob"):
        rows.append(["Date of birth", patient["dob"]])
    rows.append(["Phone", patient.get("phone") or "—"])
    rows.append(["Blood type", patient.get("blood_type") or "—"])
    rows.append(["Category", patient.get("category") or "—"])
    rows.append(["Risk level", str(patient.get("risk_level", "—")).upper()])

    hw_parts = []
    if patient.get("height_cm"):
        hw_parts.append(f"{patient['height_cm']} cm")
    if patient.get("weight_kg"):
        hw_parts.append(f"{patient['weight_kg']} kg")
    if hw_parts:
        rows.append(["Height / Weight", " · ".join(hw_parts)])

    chronic = ", ".join(patient.get("chronic_conditions") or []) or "—"
    rows.append(["Chronic conditions", chronic])

    if doctor_name:
        rows.append(["Treating doctor", f"Dr. {doctor_name}"])

    flow.append(_kv_table(rows))

    # ── Call session details ──────────────────────────────────────────────────
    if session:
        _section(flow, "Call Session", s)
        sess_rows = [
            ["Direction", session.get("direction", "—").capitalize()],
            ["Channel", session.get("channel", "—").capitalize()],
            ["Status", session.get("status", "—")],
        ]
        if session.get("started_at"):
            sess_rows.append(["Started", session["started_at"].replace("T", " ")[:19]])
        if session.get("ended_at"):
            sess_rows.append(["Ended", session["ended_at"].replace("T", " ")[:19]])
        if session.get("duration"):
            sess_rows.append(["Duration", session["duration"]])
        flow.append(_kv_table(sess_rows))

    # ── Call summary ──────────────────────────────────────────────────────────
    _section(flow, "Call Summary", s)
    flow.append(Paragraph(report.get("summary") or "—", s["GBody"]))

    # ── SOAP note ─────────────────────────────────────────────────────────────
    if report.get("soap_note"):
        _section(flow, "SOAP Note", s)
        for line in str(report["soap_note"]).split("\n"):
            flow.append(Paragraph(line or "&nbsp;", s["GBody"]))

    # ── Captured data (structured) ────────────────────────────────────────────
    sd = report.get("structured_data") or {}
    if sd:
        _section(flow, "Captured Clinical Data", s)
        rows = [["Field", "Value"]] + [[k.replace("_", " ").title(), str(v)]
                                       for k, v in sd.items() if v is not None]
        if len(rows) > 1:
            t = Table(rows, colWidths=[65 * mm, 110 * mm])
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), TEAL),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, TEAL_LIGHT]),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ]))
            flow.append(t)

    # ── Risk flags ────────────────────────────────────────────────────────────
    flags = report.get("risk_flags") or []
    if flags:
        _section(flow, "Risk Flags", s)
        flow.append(Paragraph(
            "  ·  ".join(f.replace("_", " ").upper() for f in flags),
            ParagraphStyle("Flags", parent=s["GBody"], textColor=RED,
                           fontName="Helvetica-Bold")))

    # ── Recent vitals ─────────────────────────────────────────────────────────
    if vitals:
        _section(flow, "Recent Vitals", s)
        rows = [["Type", "Value", "Unit", "Source", "Recorded"]]
        for v in vitals:
            rows.append([
                v.get("type", "—").replace("_", " ").title(),
                str(v.get("value", "—")),
                v.get("unit") or "—",
                v.get("source", "—"),
                (v.get("recorded_at") or "")[:16].replace("T", " "),
            ])
        t = Table(rows, colWidths=[40 * mm, 25 * mm, 20 * mm, 22 * mm, 48 * mm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), TEAL),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, TEAL_LIGHT]),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
        ]))
        flow.append(t)

    # ── Medications ───────────────────────────────────────────────────────────
    if medications:
        _section(flow, "Current Medications", s)
        rows = [["Medication", "Dose", "Schedule", "New Rx"]]
        for m in medications:
            new_rx = "YES" if m.get("is_new") else "—"
            rows.append([
                m.get("name", "—"),
                m.get("dose") or "—",
                m.get("schedule") or "—",
                new_rx,
            ])
        t = Table(rows, colWidths=[55 * mm, 30 * mm, 55 * mm, 15 * mm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), TEAL),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, TEAL_LIGHT]),
            ("TEXTCOLOR", (3, 1), (3, -1), ORANGE),
            ("FONTNAME", (3, 1), (3, -1), "Helvetica-Bold"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
        ]))
        flow.append(t)

    # ── Footer ────────────────────────────────────────────────────────────────
    flow.append(Spacer(1, 8 * mm))
    flow.append(HRFlowable(width="100%", thickness=0.5, color=GREY))
    flow.append(Spacer(1, 2 * mm))
    flow.append(Paragraph(
        "This report was generated by Guardian AI. It is intended to assist the "
        "treating physician and does not replace clinical judgement.",
        ParagraphStyle("Footer", parent=s["GLabel"], fontSize=8, textColor=GREY)))

    doc.build(flow)
    return path
