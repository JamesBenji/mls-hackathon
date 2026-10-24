# Guardian

AI clinical voice agent: **Gemma 4** calls patients, collects how they're
doing, and drafts reports + triage decisions for the doctor to review.

**Stack:** FastAPI (backend) · **Gemma 4 31B** via the Google Generative
Language API (clinical brain) · **ElevenLabs** (voice calls) · **Twilio**
(SMS) · Postgres / SQLite · **Flutter** (doctor dashboard).

See [plan.md](./plan.md) for the current tech stack, architecture and status.

---

## 1. Backend (FastAPI)

```bash
cd backend
python -m venv .venv
.venv/Scripts/activate          # Windows  (source .venv/bin/activate on macOS/Linux)
pip install -r requirements-dev.txt

cp ../.env.example .env         # then fill in keys (see below)
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000/docs for the interactive API.

**Config:** copy `.env.example` to `backend/.env` and fill in:

| Variable | What |
|----------|------|
| `GOOGLE_API_KEY` | Gemma 4 cloud API key (required for the clinical brain) |
| `GEMMA_MODEL` | defaults to `gemma-4-26b-a4b-it` |
| `ELEVEN_API_KEY` / `ELEVEN_AGENT_ID` / `ELEVEN_AGENT_PHONE_NUMBER_ID` | voice calls |
| `ELEVEN_WEBHOOK_SECRET` / `ELEVEN_TOOL_AUTH_TOKEN` | webhook + agent-tool auth |
| `TWILIO_*` | SMS sending + inbound webhook signature validation |
| `GUARDIAN_API_KEY` | X-API-Key required on all non-webhook routes |
| `DATABASE_URL` | Postgres in prod, `sqlite:///./guardian.db` locally |

Without telephony keys the adapters run in safe **mock mode** (calls are
simulated) so the API still works end to end.

### Key endpoints
| Method | Path | What |
|--------|------|------|
| GET  | `/health` | liveness |
| GET  | `/health/models` | Gemma API + telephony/scheduler status (X-API-Key) |
| GET/POST/PATCH | `/patients...` | patient CRUD, vitals |
| GET  | `/clinical/{id}/question-plan` | history-aware question plan (Gemma) |
| POST | `/clinical/{id}/analyze` | transcript → report + triage |
| GET  | `/triage` · POST `/triage/{id}/review` | decisions awaiting doctor review |
| POST | `/calls/{id}/start` · `/calls/webhook` | place calls, receive ElevenLabs post-call webhook |
| POST | `/inbound/sms` | Twilio inbound SMS (red-flag scan, callback jobs) |
| POST | `/agent/jobs` | doctor's natural-language call scheduler |
| GET  | `/reports/{id}` · `/reports/{id}/pdf` · POST `/reports/{id}/notify` | report detail, PDF, doctor email |

## 2. Dashboard (Flutter)

```bash
cd dashboard
flutter pub get
flutter run -d chrome        # web
```

## 3. Tests

```bash
cd backend && pytest        # in-process fakes; nothing hits the network
```
