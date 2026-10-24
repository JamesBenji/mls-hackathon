# Guardian — Local Dev

AI clinical voice-agent. See [`SCOPE.md`](./SCOPE.md) for the full spec.

Patient photo sync: see [`PHOTO_SYNC.md`](./PHOTO_SYNC.md) for secure provisioning, additive database setup, exact Flutter contract, FCM, and deployment limitations. Existing clinical routes remain unauthenticated; do not expose this entire API publicly without a separate access gateway.

Stack: **FastAPI** (backend) · **Postgres** · **MedGemma via Ollama** (brain) ·
**ElevenLabs** (calls) · **Flutter** (doctor dashboard, web + mobile).

---

## 1. Prerequisites (install once)

```bash
# Ollama + MedGemma (the clinical brain)
brew install ollama
ollama serve                 # leave running in one terminal
ollama pull medgemma:4b      # ~3.3 GB, in another terminal

# Flutter (doctor dashboard)
brew install --cask flutter
flutter doctor               # follow any prompts

# Postgres is already running locally; the `guardian` db is created.
```

## 2. Backend (FastAPI)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python -m app.seed           # DESTRUCTIVE: disposable demo DB only; drops clinical tables
# Existing DB: use python -m app.photos.admin init-schema instead (never drops data).
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000/docs for the interactive API.

**Config:** copy `../.env.example` to `backend/.env` and fill in keys.
Without keys, external services run in safe **mock mode** (calls are
simulated, MedGemma reports "down") so the API still works.

### Key endpoints
| Method | Path | What |
|--------|------|------|
| GET  | `/health` | API + MedGemma status |
| GET  | `/patients` | list (high-risk first in UI) |
| GET  | `/patients/{id}` | detail: vitals, meds, reports |
| PATCH | `/patients/{id}` | edit demographics/vitals |
| GET/POST | `/patients/{id}/vitals` | list / add a reading |
| GET  | `/clinical/{id}/question-plan` | MedGemma: history-aware questions |
| POST | `/clinical/{id}/analyze` | MedGemma: transcript → report + triage |
| GET  | `/triage` | decisions awaiting doctor review |
| POST | `/triage/{id}/review` | confirm/override model decision |

> **Telephony (ElevenLabs/Twilio) is a separate workstream** — see
> `ELEVENLABS_TWILIO_SETUP.md`. That layer calls into `app/services/clinical.py`
> (`build_question_plan`, `analyze`, `persist_analysis`), the seam between the
> phone/SMS adapters and the MedGemma clinical brain.

### Quick test (no keys needed)
```bash
# History-aware question plan (falls back to the doctor's template if
# MedGemma isn't pulled yet):
curl localhost:8000/clinical/1/question-plan

# Run the clinical brain over a transcript. Pass a call_session_id to persist.
curl -X POST localhost:8000/clinical/1/analyze \
  -H 'Content-Type: application/json' \
  -d '{"transcript":"Patient: my chest feels tight and I am dizzy."}'
```
With `ollama serve` running and `medgemma:4b` pulled, `/analyze` returns real
triage (urgency + recommended action + escalation).

## 3. Dashboard (Flutter)

```bash
cd dashboard
flutter pub get
flutter run -d chrome        # web
# or: flutter run             # mobile (change apiBase to your LAN IP)
```

The dashboard lists patients (high-risk first) and opens a detail view
(vitals, meds, past reports = agent memory).

---

## Project layout
```
backend/app/
  main.py              FastAPI app: patients, vitals, triage, clinical endpoints
  services/
    clinical.py        interface the telephony layer calls (wraps MedGemma)
  agent.py             MedGemma reasoning (question plan, analyze)
  medgemma.py          Ollama client (graceful fallback)
  models.py            SQLAlchemy data model
  seed.py              demo data
dashboard/lib/main.dart  Flutter doctor dashboard (separate workstream)
```

**Workstreams:** backend clinical core (this app) · Flutter dashboard ·
ElevenLabs/Twilio telephony (`ELEVENLABS_TWILIO_SETUP.md`). Telephony plugs
into `app/services/clinical.py` so the three stay decoupled.

---

## 4. Deploying to DigitalOcean

MedGemma runs through Ollama, which deploys to DO cleanly. Recommended
layout: **Ollama + FastAPI on the same droplet**, expose only the backend
(port 8000); the backend reaches Ollama at `localhost:11434`. One machine,
clean security.

### GPU Droplet (recommended — MedGemma inference is fast)
```bash
# On a fresh GPU Droplet (Ubuntu):
curl -fsSL https://ollama.com/install.sh | sh
ollama pull medgemma:4b
OLLAMA_HOST=0.0.0.0:11434 ollama serve   # or keep on localhost (see below)
```
`medgemma:4b` is ~3.3 GB quantized, so a smaller GPU is enough. GPU droplets
bill by the hour — spin up for demos/testing, destroy after.

### CPU Droplet (works, slow)
Ollama runs CPU-only on an 8GB+ droplet. Fine for the (non-real-time)
post-call report step; too slow for live interactive turns.

### Pointing the backend at it
Only one env var changes — the backend already reads `MEDGEMMA_URL`:
```
# backend/.env
MEDGEMMA_URL=http://localhost:11434     # same-droplet (recommended)
# or http://<droplet-ip>:11434          # separate machines
```

### ⚠️ Security — Ollama has NO auth by default
If you bind Ollama to `0.0.0.0` on a public droplet, anyone can use it.
Pick one:
- **Same-droplet + localhost** (simplest): don't expose 11434 at all; only
  open port 8000 for the backend.
- **DO cloud firewall**: allow 11434 only from the backend's IP.
- **Reverse proxy** (nginx/Caddy) with an auth token in front of Ollama.

### Other pieces on DO
- **Postgres**: use a DO Managed Database; set `DATABASE_URL` in `.env`.
- **Backend**: run `uvicorn` behind nginx, or containerize it.
- **Flutter web**: `flutter build web` → serve the static `build/web` via
  nginx or DO App Platform / Spaces.
