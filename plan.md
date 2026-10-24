# Guardian — Plan & Current State

> Single source of truth for the tech stack and what we're building **right
> now**. Written for teammates and AI assistants joining mid-project — read
> this first. Last updated: 2026-10-24.

## What we are building

An AI clinical voice agent for post-op / chronic patients. Guardian **calls
patients** (ElevenLabs), asks the doctor's questions plus history-aware
follow-ups, extracts vitals and symptoms from the conversation (**Gemma 4**),
scans for red flags, and drafts a **report + triage decision** for the doctor
to review in the **Flutter dashboard**. Doctors can also text free-form
instructions ("call Maria tomorrow at 9, ask about dizziness") to their
agent. Everything the model produces is a **draft for the doctor** — nothing
is ever auto-applied to a patient.

## Current tech stack (post-pivot, 2026-10-24)

| Layer | Choice |
|-------|--------|
| Clinical brain | **Gemma 4 26B A4B** (`gemma-4-26b-a4b-it`, MoE — fast + reliable) via the **Google Generative Language API** (cloud, API key). The 31B (`gemma-4-31b-it`) exists but was 500ing server-side as of 2026-10-24; switch via `GEMMA_MODEL` when it stabilizes. |
| Voice calls | **ElevenLabs** Conversational AI (agent + imported Twilio number) |
| SMS | **Twilio** (outbound send + inbound webhooks with signature validation) |
| Backend | **FastAPI** + SQLAlchemy 2 + Pydantic v2, APScheduler (in-process jobs) |
| DB | Postgres in prod, SQLite locally/tests (schema auto-created at startup) |
| Dashboard | **Flutter** (web + mobile) |
| ~~Old stack~~ | ~~MedGemma via Ollama on a DigitalOcean GPU droplet~~ — **dropped** |

## Architecture

```
backend/app/
  adapters/            outbound integrations, no business logic
    gemma_client.py    Google Gen-Language API: chat / chat_json (schema in
                       prompt + one validation retry), bounded 5xx retries
                       (the hosted Gemma models 500 intermittently under
                       load — identical payloads succeed on re-roll),
                       concurrency gate, health probes. Same contract the
                       old Ollama client had.
    elevenlabs.py      calls + agent provisioning; twilio_sms.py: SMS
  services/            the business logic
    clinical.py        question plans, report extraction, triage, call-job
                       extraction. Model output is always guarded by
                       deterministic post-checks (see below).
    redflags.py       deterministic red-flag scan (EN/DE), negation-aware,
                       numeric BP/glucose parsing. Independent of any LLM;
                       sets a MINIMUM urgency the model cannot lower.
    call_orchestrator.py  call lifecycle: attempts, retries, post-call pipeline
    scheduler.py       APScheduler: callbacks, retries, doctor-scheduled calls
    notify.py / pdf.py / email.py   doctor notification (PDF + Gmail SMTP)
  routers/             HTTP layer (patients, calls, triage, clinical,
                       agent_jobs, sms, inbound_sms, health, webhooks)
  photos/              authenticated patient photo sync (device tokens, FCM)
dashboard/             Flutter doctor dashboard
```

**Auth model:** X-API-Key on dashboard routes · ElevenLabs HMAC on
`/calls/webhook` · Bearer token on `/tools/*` · Twilio signature on
`/inbound/sms` · per-device tokens on `/photos/*` · `/health` is open.

**Safety rails (deterministic, model cannot bypass):**
- Question-plan post-check: doctor's questions always kept, ≤3 follow-ups,
  always a red-flag probe, no diagnostic statements.
- Extraction guard: a number only survives into the report if the PATIENT
  said it in the transcript.
- Triage floor: red-flag hits set a minimum urgency of "urgent"; the LLM
  can only raise it. Suicidal statements always escalate.
- Model failures fall back (doctor's static questions, rules-only triage);
  nothing crashes a call.

## What works right now

- ✅ Full call pipeline: ElevenLabs outbound call → transcript → Gemma 4
  report + triage → doctor review; post-call webhook + agent tools
- ✅ Inbound SMS: red-flag scan, "call me back" jobs, retries
- ✅ Doctor's agent: natural-language call scheduling (patient resolution
  is restricted to the requesting doctor's own patients)
- ✅ Report PDF + doctor email (Gmail SMTP)
- ✅ Patient photo sync (private storage, per-device auth, FCM push)
- ✅ Deterministic clinical guards + red-flag engine (EN/DE) with tests
- ✅ Scheduler: callbacks / retries persist across restarts (DB jobstore)

## In progress / next

- ✅ Model id verified against the live API (2026-10-24): `gemma-4-26b-a4b-it`
  is fast and reliable; the 31B 500s on Google's side. Live smoke test:
  `cd backend && .venv/Scripts/python.exe -m scripts.smoke_gemma`
- ✅ Live end-to-end verified (2026-10-24): smoke test triages a
  hypertensive-emergency transcript to `emergency / ambulance` via the
  cloud API. The API also returns transient 500s under load (fast
  failures); the client now retries 5xx up to 2× inside the caller's
  timeout. Diagnosis matrix: `scripts/debug_triage_500.py`.
- ✅ Full test suite green after the Ollama→Gemma-API swap (639 passed).
- ☐ Real ElevenLabs agent end-to-end over the phone (provisioning script:
  `backend/scripts/provision_elevenlabs.py`).
- ☐ Deploy (any container host + managed Postgres; no GPU needed anymore —
  the model is cloud-hosted).

## Environment

Copy `.env.example` → `backend/.env`. Required for the real thing:
`GOOGLE_API_KEY`, `ELEVEN_API_KEY`, `ELEVEN_AGENT_ID`,
`ELEVEN_AGENT_PHONE_NUMBER_ID`, `ELEVEN_WEBHOOK_SECRET`,
`ELEVEN_TOOL_AUTH_TOKEN`, `TWILIO_*`, `GUARDIAN_API_KEY`, `DATABASE_URL`.
Anything missing → that adapter runs in mock mode and logs instead.

## Conventions

- All datetimes are naive UTC in the DB; clinic-local time is Europe/Berlin
  (`app/timeutil.py`).
- Model calls go through `app.adapters.gemma_client` by module attribute
  (tests monkeypatch `chat`/`chat_json`); settings are read at call time.
- Logs never contain transcripts, prompts, or model output — error types
  and counts only.
- `python -m app.seed` resets the schema and loads demo data (DESTRUCTIVE).
