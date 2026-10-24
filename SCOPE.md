# 🛡️ Guardian — Scope & Implementation Plan

> **Companion to [`GUARDIAN.md`](./GUARDIAN.md)** (the pitch/brainstorm). This doc is the **source of truth for what we build**: core functionality, data model, and the step-by-step implementation plan.

**Elevator pitch:** Guardian is an AI clinical agent that **phones patients**, runs a doctor-defined check-in interview, asks intelligent follow-ups using the patient's history, captures vitals and medication data, and delivers a **detailed report** to the doctor's dashboard + email (with downloadable PDF). Built on **ElevenLabs** (voice calls), **MedGemma on DigitalOcean** (private clinical brain), and **Twilio** (SMS).

---

## 📋 Table of Contents
1. [Problem Context](#-problem-context)
2. [Design Principles](#-design-principles)
3. [Scope: Phase 1 vs Phase 2](#-scope-phase-1-vs-phase-2)
4. [Core User Story](#-core-user-story)
5. [The Phone Call Flow](#-the-phone-call-flow)
6. [Interview Question Bank](#-interview-question-bank)
7. [The "Call Me Back" Flow](#-the-call-me-back-flow-vitals-retake)
8. [Doctor Dashboard](#-doctor-dashboard)
9. [Data Model](#-data-model)
10. [System Architecture](#-system-architecture)
11. [Tech Stack](#-tech-stack-locked)
12. [Implementation Plan](#-implementation-plan)
13. [API Surface](#-api-surface)
14. [Phase 2: Computer Vision](#-phase-2-computer-vision-x-ray-recovery-tracking)
15. [Risks & Fallbacks](#-risks--fallbacks)

---

## 🩺 Problem Context

A patient recently discharged from hospital still needs follow-up, but the doctor has **~100 patients** and no time to follow up on all of them. Guardian automates these check-ups via **phone calls** (and text), freeing the doctor to focus on the cases that actually need attention.

- The patient receives an **automated call from an AI agent** that introduces itself and asks the questions the doctor configured.
- The agent **collects data** and, based on the patient's input, **adapts** — skipping questions when answers are already evident, and triaging urgency.
- If input signals danger (patient feels very bad, medication clearly not working), the agent informs the patient that it will have the doctor **call back immediately**, **send an ambulance**, or **let the patient request an appointment**.
- **Patients interact only via phone or text.** They can also **call the doctor's agent** to report an issue, or text **"call"** to be called back.
- The system decides whether the doctor must be **alerted for immediate action**; either way the doctor gets a **report** to review (and to sanity-check the model's decision, since the model has limited patient data).
- The doctor can **speak or type** to their agent to schedule new call jobs (patient, situation, treatment, questions). Speaking tends to yield richer context.
- On **successive calls** the agent references the last call and offers a summary; the patient can **reschedule** or **request more calls**.

## 🧱 Design Principles

> **The patient-facing interface is swappable. Keep the core backend clean so new interaction channels (website, mobile app, etc.) plug in without touching core logic.**

- **Channel-agnostic core.** Call orchestration, triage, memory, and reporting live in the backend and know nothing about *how* the patient is reached. Phone/SMS today; web/app later — same core.
- **Interaction adapters.** Each channel (ElevenLabs voice, Twilio SMS, future web/app) is a thin adapter that translates to/from the core's channel-neutral messages.
- **Two agent personas, one brain:** the **patient check-in agent** (collects data, triages) and the **doctor's personal agent** (creates jobs from voice/text). Both are MedGemma-driven.
- **Human-in-the-loop by design.** The model triages but the doctor always receives a report and can override — the model is explicitly assumed to have incomplete data.

---

## 🎯 Scope: Phase 1 vs Phase 2

### ✅ Phase 1 — CORE (build this first)
- **Outbound AI phone calls** (ElevenLabs native calling)
- Agent asks **doctor-defined questions** + **AI follow-ups** informed by patient history
- **Adaptive questioning**: agent may *skip* remaining questions when input already answers them or signals distress (e.g., patient says they feel very bad, or clearly isn't responding to medication)
- The agent **may answer basic patient questions** during the call (we chose the richer path over data-collection-only — see Design Principles)
- **Structured data capture** during the call: blood pressure, blood sugar, weight, medication adherence, sleep, side effects, symptoms
- **Triage decision**: based on input, agent decides whether the doctor must be alerted for immediate action, and can tell the patient the doctor will **call back immediately**, **send an ambulance**, or **let the patient request an appointment**
- **"Call me back" flow**: if a vital isn't available, agent schedules a callback (e.g., "take your BP, I'll call back in 15 min")
- **Patient-initiated interaction**: patient can call/text the **doctor's agent number** to report an issue or text **"call"** to be called back
- **Scheduling control by patient**: patient can decline now and state a later time, or request more calls
- **Missed-call handling**: if the patient doesn't answer, the system **texts the next call time**; patient can text **"call"** to trigger a callback
- **Successive-call continuity**: on later calls the agent references the last call and offers a **summary of the previous discussion**
- **Doctor's personal agent**: doctor **speaks (or types)** to their agent via the web account to create call jobs — patient, current situation, medication/treatment, questions to ask
- **Detailed clinical report** generated by MedGemma after each call (inbound or outbound)
- **Doctor notification**: email summary + link to dashboard + downloadable PDF
- **Doctor dashboard**: patient list, categories, patient detail (vitals, chronic conditions, history), visit records, transcription records, past call reports, upcoming in-person meetings
- **Editable patient vitals** on the dashboard (blood type, height, weight, BP, diabetes markers)
- **Call history as agent memory**: past reports become context for the next call's follow-ups
- **SMS** (Twilio): medication reminders, health tips, callback notices, missed-call times, photo-upload links

### 🔭 Phase 2 — SCALE (after core works)
- **Photo intake**: patient sends images via SMS, **or** requests a website link where the photo is uploaded (the system auto-identifies the patient and links it to the doctor's interface)
- **File upload** (X-ray, wound photos) via the app
- **MedGemma vision**: assess bone/wound recovery across time periods ("compare this X-ray to last month's")
- Automated **medication reminders** + **AI health tips** via SMS on a schedule
- **Additional interaction channels** plugged into the same core (web, mobile app) — see Design Principles

---

## 👤 Core User Story

> **Dr. Lee** sets up a check-in for patient **Maria** (post-op, hypertensive, diabetic). At the scheduled time, Guardian **calls Maria**. It greets her, asks the doctor's questions, and — seeing from her history that she had high BP last week — **asks a targeted follow-up**. Maria hasn't taken her BP today, so Guardian says *"please take it now, I'll call you back in 15 minutes."* It calls back, records the new reading, finishes the interview, and generates a **detailed report**. Dr. Lee gets an **email** — *"Call with Maria Gomez completed at 14:30, Oct 24. Summary: BP improved, reports mild dizziness. Full report on dashboard · Download PDF"* — and sees everything on his **dashboard**, with Maria's vitals auto-updated.

---

## 📞 The Phone Call Flow

```
1. Trigger (scheduled or doctor-initiated)
        │
2. Load patient context ──────► patient record + last N call reports (agent memory)
        │
3. MedGemma builds the question plan
   (doctor's required questions + history-aware follow-ups)
        │
4. ElevenLabs places the call ──► patient answers
        │
5. Live interview:
   - ask doctor-defined questions
   - capture vitals / meds / sleep / side effects
   - AI follow-ups based on answers + history
   - if a vital is missing → schedule "call me back" (see below)
        │
6. Call ends
        │
7. MedGemma generates:
   - structured data (vitals, adherence) → update patient record
   - detailed clinical report + SOAP note
   - risk flags / escalation if needed
        │
8. Persist report → Postgres
        │
9. Notify doctor: email (summary + PDF) + dashboard update
```

**Hybrid MedGemma placement (latency-aware):** ElevenLabs runs the live conversation using a MedGemma-generated question plan; MedGemma also does the heavy post-call reasoning (report, extraction, risk). This avoids per-turn LLM latency on the live call while keeping the intelligence.

---

## 🗂 Interview Question Bank

Questions the agent should be able to ask (doctor can enable/customize per patient):

**Vitals**
- "Did you take your blood pressure today?" → if no → *callback flow*; if yes → record systolic/diastolic
- "Did you check your blood sugar? What was the reading?"
- "What's your current weight?"

**Medication**
- "Did you take your medication today?"
- "What time did you take it?"
- "Any side effects?" *(if new prescription → probe side effects; otherwise → ask general feeling/improvement)*
- "Have you noticed any improvement since starting the medication?"

**Wellbeing**
- "How many hours did you sleep last night?"
- "How are you feeling overall today?"
- Symptom probes + red-flag detection (chest pain, breathing difficulty, etc.)

**History-aware follow-ups (generated by MedGemma from past reports)**
- "Last week you mentioned a cough — is it better or worse?"
- "Your BP was high on our last call — has it come down?"

---

## 🔁 The "Call Me Back" Flow (vitals retake)

```
Agent: "Did you take your blood pressure today?"
Patient: "No."
Agent: "No problem — please take it now. I'll call you back in 15 minutes
        to record it and finish our chat."
   │
   ├─► schedule callback (T+15min)
   ├─► send SMS reminder (Twilio): "Guardian will call back at 14:45 for your BP"
   │
   └─► [15 min later] agent re-calls, resumes interview at the saved checkpoint,
        records the new value, updates the patient record.
```
Same pattern for blood sugar, weight, or any value the patient needs to go measure.

---

## 🖥 Doctor Dashboard

**Main view**
- **Patient list** with **categories** (e.g., post-op, chronic/hypertension, diabetes, high-risk)
- Risk badges; high-risk patients surfaced first
- **Upcoming in-person meetings** schedule

**Patient detail view**
- **Demographics & clinical:** blood type, chronic conditions (maladies), height, weight, BP, diabetes markers — **all editable**, with history/trend tracking
- **Visit records** (in-person)
- **Medical transcription records**
- **Past agent call reports** (these double as agent memory/context)
- Vitals **trend charts** (weight, BP, blood sugar over time)
- Next scheduled call / meeting

**Notifications**
- Email on each completed call (summary + dashboard link + PDF download)

---

## 🗃 Data Model

```
doctors
  id, name, email, password_hash

patients
  id, doctor_id, name, phone, dob, blood_type,
  height_cm, weight_kg, chronic_conditions[], category,
  risk_level, created_at

vitals               (time-series, editable)
  id, patient_id, type (bp_systolic|bp_diastolic|blood_sugar|weight|...),
  value, unit, recorded_at, source (call|manual|sms)

medications
  id, patient_id, name, dose, schedule, is_new_prescription,
  started_at

visits               (in-person)
  id, patient_id, date, notes, transcription_id?

transcriptions
  id, patient_id, source (visit|call), text, created_at

call_sessions
  id, patient_id, direction (outbound|inbound), channel (voice|sms),
  started_at, ended_at, status
  (scheduled|in_progress|callback_pending|completed|failed|missed),
  callback_at?, checkpoint (resume state for call-me-back)

call_reports         (agent memory lives here)
  id, call_session_id, patient_id, summary, soap_note,
  structured_data (json: captured vitals/meds/sleep...),
  risk_flags[], escalated (bool), pdf_path, created_at

triage_decisions
  id, call_session_id, patient_id,
  urgency (none|monitor|urgent|emergency),
  recommended_action (none|doctor_callback|ambulance|request_appointment),
  rationale, doctor_reviewed (bool), created_at

call_jobs            (created by the doctor's personal agent)
  id, doctor_id, patient_id, source (voice|typed),
  situation, treatment, questions[], scheduled_at,
  status (pending|scheduled|done), created_at

question_templates
  id, doctor_id, patient_id?, questions[] (enabled/custom)

appointments         (upcoming in-person meetings)
  id, patient_id, doctor_id, scheduled_at, type, notes

photo_uploads        (SMS image or upload-link; Phase 2 vision)
  id, patient_id, call_session_id?, url, upload_token,
  source (sms|web_link), assessment?, created_at

sms_log
  id, patient_id, body, sent_at, kind (reminder|tip|callback)
```

---

## 🏛 System Architecture

```
     PATIENT (phone / SMS only)        DOCTOR (web + mobile)
            │                                   │
            ▼                                   ▼
┌───────────────────────┐          ┌────────────────────────┐
│ Channel Adapters       │          │ Flutter app (doctor)    │
│ (SWAPPABLE):           │  REST    │  - dashboard            │
│  ElevenLabs voice      │◄────────►│  - speak/type to agent  │
│  Twilio SMS            │  (poll)  └────────────────────────┘
│  [future: web, app]    │                      │
└──────────┬─────────────┘                      │
           │ channel-neutral messages           │
           ▼                                     ▼
          ┌──────────────────────────────────────────┐
          │ FastAPI CORE (channel-agnostic, on DO)     │
          │  - call orchestration (in/outbound)        │
          │  - triage engine (urgency + action)        │
          │  - agent memory (past reports)             │
          │  - patient/vitals/report CRUD + scheduler  │
          │  - doctor's-agent job creation             │
          └───┬───────────────┬───────────────┬────────┘
              │               │               │
              ▼               ▼               ▼
   ┌──────────────────┐ ┌──────────────┐ ┌──────────────────┐
   │ MedGemma on DO    │ │ Postgres (DO  │ │ Email + PDF       │
   │ (both agents:     │ │ managed DB)   │ │ (SendGrid/Resend) │
   │  patient check-in │ └──────────────┘ └──────────────────┘
   │  + doctor's agent)│
   └──────────────────┘
```

> **Swappability:** the patient reaches the system only through **channel adapters**. Phone/SMS today; web/app later — the FastAPI core never changes.


---

## 🧰 Tech Stack (LOCKED)

> **Non-negotiable core:** **Gemma 4 (MedGemma)** + **ElevenLabs**. Everything else below is supporting infrastructure.

| Layer | Choice |
|-------|--------|
| **Phone calls** | ElevenLabs native calling (outbound **and** inbound — patient can call the agent) |
| **Clinical brain** | MedGemma on a DigitalOcean GPU droplet (Ollama) — **hybrid**: question plan + post-call report/extraction |
| **SMS** | Twilio |
| **Backend** | Python / FastAPI (SQLAlchemy) |
| **Frontend** | Flutter (web + mobile, single codebase) |
| **Database** | Postgres (DigitalOcean managed) |
| **Email + PDF** | Real email (SendGrid/Resend) + real generated PDF |
| **Scheduler** | APScheduler / background worker (callbacks, SMS reminders) |

---

## 🏗 Implementation Plan

> Ordered to keep a working slice at each step. Parallelizable across a team — infra + backend + frontend tracks.

### Milestone 0 — Foundations (infra + skeleton)
1. **DO Postgres** provisioned; connection string in `.env`.
2. **DO GPU droplet** + MedGemma via Ollama; verify curl completion (fallback: base `gemma2`).
3. **FastAPI skeleton** + SQLAlchemy models + Alembic migrations for the data model above.
4. **Seed data**: 4–5 fake patients across categories, with history + past reports (so the agent has memory to use in the demo).
5. **Flutter skeleton** (web + mobile) + routing + REST API client (polling for live updates).

### Milestone 1 — Dashboard (read-only)
6. Patient list + categories + risk badges.
7. Patient detail view: demographics, vitals, meds, visits, transcriptions, past reports, upcoming appointments.
8. Vitals trend charts.

### Milestone 2 — The phone call (the heart)
9. **ElevenLabs agent config**: question plan injection, transcript webhook.
10. `POST /calls/start` → load context → MedGemma builds question plan → place outbound call.
11. **Call status + transcript webhooks** → persist `call_session` + `transcription`.
12. **Post-call report**: MedGemma → summary, SOAP, structured extraction (vitals/meds/sleep), risk flags.
13. **Auto-update patient record** from extracted structured data.

### Milestone 3 — Memory, follow-ups & triage
14. Feed last N `call_reports` into the question-plan prompt → history-aware follow-ups.
15. **Adaptive questioning**: let the agent skip questions when answers are already evident.
16. **Triage decision**: MedGemma classifies urgency + recommended action (doctor callback / ambulance / request appointment); persist `triage_decision`; surface to doctor for review (human-in-the-loop).
17. Red-flag detection → escalation flag on report.

### Milestone 4 — Callback flow
18. Detect "vital unavailable" → create `callback_pending` + checkpoint state.
19. Scheduler re-places the call at T+interval; resume interview from checkpoint.
20. Twilio SMS: callback notice.

### Milestone 5 — Patient-initiated & missed-call handling
21. **Inbound SMS/voice adapters**: patient texts "call" or a preferred time; patient calls the doctor's agent number to report an issue.
22. **Missed call** → SMS the next call time; "call" triggers a callback.
23. **Successive-call continuity**: agent references the last call and offers a summary.

### Milestone 6 — Doctor's personal agent
24. Doctor **speaks or types** to their agent via web → MedGemma extracts a `call_job` (patient, situation, treatment, questions) → schedule.

### Milestone 7 — Doctor notification
25. PDF report generation.
26. Email on call completion (summary + dashboard link + PDF attachment/download).

### Milestone 8 — Editable dashboard + SMS
27. Editable vitals/demographics (write-back to Postgres with history).
28. Twilio SMS: medication reminders + AI tips (scheduled).

### Milestone 9 — Polish + demo
29. Demo script, seed a compelling patient, "replay" safety net, rehearse.

---

## 🔌 API Surface (first pass)

```
# Patients
GET    /patients                 list (filter by category/risk)
GET    /patients/{id}            detail
PATCH  /patients/{id}            edit demographics/vitals
GET    /patients/{id}/vitals     time-series
POST   /patients/{id}/vitals     add reading
GET    /patients/{id}/reports    past call reports (agent memory)

# Calls
POST   /calls/start              initiate outbound call (patient_id)
POST   /calls/webhook            ElevenLabs/Twilio status/transcript webhook
                                 (handles outbound + inbound voice & SMS)
GET    /calls/{id}               session + report
POST   /calls/{id}/callback      schedule a retake callback

# Inbound / patient-initiated (channel adapters hit these)
POST   /inbound/voice            patient calls the doctor's agent number
POST   /inbound/sms              patient texts (e.g., "call", a time, a photo)

# Doctor's personal agent (job creation via voice or text)
POST   /agent/jobs              create call job from voice transcript or typed
                                (patient, situation, treatment, questions)
GET    /agent/jobs              list jobs (pending|scheduled|done)

# Triage (human-in-the-loop review)
GET    /triage                  decisions needing doctor review
POST   /triage/{id}/review      doctor confirms/overrides the model decision

# Reports
GET    /reports/{id}             full report
GET    /reports/{id}/pdf         download PDF

# Appointments
GET    /appointments             upcoming in-person meetings
POST   /appointments             schedule

# Photos (SMS image or upload-link; Phase 2 vision)
POST   /photos/link              issue a tokenized upload link for a patient
POST   /photos/upload            receive photo (from SMS or web link),
                                 auto-identify patient, link to doctor
GET    /patients/{id}/photos     patient's uploaded images + assessments

# SMS
POST   /sms/send                 reminder/tip/callback/missed-call/photo-link (Twilio)
```

---

## 🔭 Phase 2: Computer Vision (X-ray recovery tracking)

- **File upload** in the app (X-ray / wound photo), tied to a patient + date.
- **MedGemma vision** assesses the image and **compares against prior uploads** to track recovery ("callus forming, alignment maintained vs. 4 weeks ago").
- Timeline view on the dashboard: image series + MedGemma's recovery assessment per interval.
- Agent can reference imaging in calls ("your last X-ray showed good healing — any pain when you put weight on it?").

---

## ⚠️ Risks & Fallbacks

| Risk | Mitigation |
|------|-----------|
| **Outbound calling setup eats time** | Verify ElevenLabs native calling places one real call in Milestone 0. If blocked, demo with a single pre-configured number. |
| **Per-turn LLM latency on live call** | Hybrid design: question plan pre-built; MedGemma heavy-lifting is post-call, not per-turn. |
| **MedGemma serving/VRAM** | Fallback chain: MedGemma text-only → base `gemma2`. Verify hour 0. |
| **Callback timing in a short demo** | Make the callback interval configurable; use a short interval (e.g., 1 min) for the live demo. |
| **Scope is large for a hackathon** | Phase 1 only. Within Phase 1, the **call → report → email/dashboard** path is the MVP; everything else is additive. |
| **Twilio/email deliverability** | Test with the team's own numbers/emails early; keep a mock toggle as backup. |

---

## ✅ Decisions Locked
- **Calls:** ElevenLabs native calling (outbound)
- **MedGemma:** Hybrid (question plan + post-call report/extraction), on DO GPU droplet
- **SMS:** Twilio (real)
- **DB:** Postgres on DigitalOcean (managed)
- **Backend:** Python / FastAPI
- **Frontend:** Flutter (web + mobile, single codebase; REST + polling)
- **Email + PDF:** Real email + real generated PDF
- **Phase 1 = phone-first core; Phase 2 = computer vision (X-ray recovery)**
