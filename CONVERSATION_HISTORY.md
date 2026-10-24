# Guardian — Conversation & Decision History

> A running log of the Claude Code sessions that built Guardian, so you (and
> your team) can pick up context later. Newest context at the bottom.
> Last updated: 2026-10-24.

---

## 1. What Guardian is

An AI clinical **voice agent** that phones recently-discharged patients, runs a
doctor-defined check-in interview (with history-aware follow-ups), captures
vitals/medication data, triages urgency, and reports back to the doctor via a
dashboard + email (with PDF). Built for a 5-hour hackathon.

**Core pitch:** private, medical-grade — MedGemma runs on our own
infrastructure, so patient data never touches a third-party cloud LLM.

**Sponsor tools:** Gemma 4 / **MedGemma** (clinical brain), **ElevenLabs**
(voice calls), **DigitalOcean** (hosting). SMS via **Twilio**.

Repo: https://github.com/JamesBenji/mls-hackathon

---

## 2. How the idea evolved (chronological)

1. **Brainstorm** — explored healthcare + agent ideas. Landed on "Guardian," a
   post-discharge check-in voice agent, because a ringing phone + live risk
   dashboard makes a strong demo and all three sponsor tools are load-bearing.
2. **Guardian + Translator fusion** — briefly considered multilingual check-ins;
   settled on English for the demo (zero live translation risk), multilingual as
   a talking point.
3. **MedGemma upgrade** — discovered MedGemma (medical-tuned, multimodal) unlocks
   wound/X-ray image assessment. Became the Phase 2 headline.
4. **Phone-first pivot (major)** — refocused on real outbound phone calls + SMS,
   a full doctor dashboard, and a rich data model. Computer vision (X-ray
   recovery) pushed to Phase 2.
5. **Colleague's problem context added** — the ~100-patients framing, adaptive
   questioning, triage (callback/ambulance/appointment), patient-initiated
   calls/SMS, missed-call handling, the doctor's personal agent, and the key
   **swappable channel-agnostic core** design principle.
6. **Division of labor set** — backend (me + user), Flutter frontend
   (Colleague A), ElevenLabs/Twilio/DigitalOcean telephony (Colleague B).
7. **Backend refocused** — removed my placeholder telephony stub; added a clean
   `services/clinical.py` seam for Colleague B's telephony to call into.

---

## 3. Locked decisions

| Area | Decision |
|------|----------|
| Product | Guardian — post-discharge check-in voice agent |
| Phase 1 | Phone calls + SMS + dashboard + clinical reports |
| Phase 2 | Computer vision (X-ray/wound recovery) via MedGemma vision |
| Calls | ElevenLabs + Twilio (Colleague B owns this) |
| MedGemma | `medgemma:4b` via **Ollama** (vision-capable); Transformers fallback |
| MedGemma placement | Hybrid — question plan pre-call + report/triage post-call |
| SMS | Twilio (real) |
| Database | Postgres (local `guardian` db; DO managed in prod) |
| Backend | Python / FastAPI / SQLAlchemy |
| Frontend | Flutter (web + mobile), REST + polling (Colleague A) |
| Email + PDF | Real email (SendGrid/Resend) + real generated PDF |
| Demo language | English |
| Design principle | Channel-agnostic core; interfaces are swappable adapters |
| Agent behavior | May answer basic patient questions (not collect-only) |

---

## 4. Workstream split

- **Backend clinical core** (this work): data model, patient/vitals CRUD,
  MedGemma reasoning, `services/clinical.py` seam, notifications (PDF/email).
- **Flutter dashboard** (Colleague A): patient list + detail views.
- **Telephony** (Colleague B): ElevenLabs agent + Twilio, webhooks, mid-call
  tools (`schedule_callback`, `flag_emergency`), scheduler, inbound SMS/voice.
  Spec lives in `ELEVENLABS_TWILIO_SETUP.md`. It calls into
  `app/services/clinical.py`.

---

## 5. Repo structure (current)

```
GUARDIAN.md                  pitch / brainstorm brief
SCOPE.md                     product scope + data model + implementation plan
ELEVENLABS_TWILIO_SETUP.md   telephony integration spec (Colleague B)
README.md                    local dev + DigitalOcean deploy notes
CONVERSATION_HISTORY.md      this file
.env.example                 all env vars (placeholders only)
.gitignore
backend/
  requirements.txt
  app/
    main.py                  FastAPI: patients, vitals, triage, clinical
    config.py                env settings (ELEVEN_*, TWILIO_*, MOCK_TELEPHONY...)
    database.py              SQLAlchemy engine/session
    models.py                full data model (14 tables)
    medgemma.py              Ollama client (graceful fallback)
    agent.py                 MedGemma reasoning (question plan, analyze)
    services/
      clinical.py            seam telephony calls into (wraps agent.py)
    seed.py                  demo data (Dr. Lee + 3 patients w/ history)
dashboard/
  pubspec.yaml
  lib/main.dart              Flutter doctor dashboard (Colleague A)
```

---

## 6. Backend API (current)

```
GET   /health                          API + MedGemma status
GET   /patients                        list
GET   /patients/{id}                   detail (vitals, meds, reports)
PATCH /patients/{id}                   edit demographics/vitals
GET   /patients/{id}/vitals            list readings
POST  /patients/{id}/vitals            add a reading
GET   /triage                          decisions awaiting doctor review
POST  /triage/{id}/review              confirm/override model decision
GET   /clinical/{id}/question-plan     MedGemma: history-aware questions
POST  /clinical/{id}/analyze           MedGemma: transcript -> report + triage
```

`services/clinical.py` exposes for the telephony layer:
`build_question_plan(db, patient_id)`, `analyze(db, patient_id, transcript)`,
`persist_analysis(db, call_session_id, patient_id, transcript, analysis)`.

---

## 7. Environment setup notes

- **Local Postgres** 16 running; `guardian` db created; user `I767867`, no password.
- **Python 3.14** only interpreter available → deps are UNPINNED in
  requirements.txt so pip grabs 3.14-compatible wheels.
- **Ollama** installed via `brew install ollama`; run `ollama serve` +
  `ollama pull medgemma:4b` (3.3 GB — slow to download).
- **Flutter** installed via `brew install --cask flutter` (first attempt hit a
  stale download lock; cleared and reinstalled).
- Run backend: `cd backend && source .venv/bin/activate &&
  uvicorn app.main:app --reload --port 8000` → http://localhost:8000/docs

### MedGemma on DigitalOcean (answered)
Yes — deploy via GPU Droplet (`curl -fsSL https://ollama.com/install.sh | sh`,
`ollama pull medgemma:4b`). Recommended: Ollama + FastAPI on the SAME droplet,
expose only port 8000, backend reaches Ollama at localhost:11434. Ollama has NO
auth by default — never expose 11434 publicly without a firewall/proxy.

---

## 8. Security / housekeeping reminders

- ⚠️ **An ElevenLabs API key was pasted in chat and must be ROTATED** (user
  chose to do it later). Never commit keys; use `backend/.env` (gitignored).
- `.gitignore` covers `.env`, `.venv/`, `.claude/settings.local.json`.
- User preference: **do not auto-commit** — user tests locally, then commits.
  (Commits in this session were done only when explicitly asked.)

---

## 9. Key commits

- `GUARDIAN.md` — pitch brief
- `SCOPE.md` — phone-first scope + data model + plan
- Switch frontend to Flutter
- Incorporate colleague's problem context + behaviors
- `.gitignore` + `.env.example`
- Scaffold backend (FastAPI core) + Flutter dashboard
- `c47d8e4` (Colleague B) — ElevenLabs + Twilio setup doc
- `75a4637` — Refocus backend on clinical core; add `services/clinical.py` seam

---

## 10. Where things stand / next steps

**Done:** backend clinical core + CRUD + clinical seam, committed & pushed.
Dashboard "Start check-in call" button removed (dead endpoint; calls are
scheduler-driven, not a UI action).

**In progress:** doctor notifications — PDF (reportlab installed) + email
(SendGrid/Resend, mock fallback) + download endpoints.

**Pending:**
- Notifications feature (PDF/email) — being built now.
- MedGemma download to finish → real triage demo.
- Colleague B builds telephony against `clinical.py`.
- Colleague A builds out the Flutter dashboard.
- Rotate the exposed ElevenLabs key.
