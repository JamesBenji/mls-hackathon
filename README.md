# Guardian — Local Dev

AI clinical voice-agent. See [`SCOPE.md`](./SCOPE.md) for the full spec.

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

python -m app.seed           # create tables + seed 3 demo patients
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
| POST | `/calls/start` | build question plan → place call |
| POST | `/calls/{id}/analyze` | transcript → MedGemma → report + triage |
| GET  | `/calls/{id}` | session + report + triage |
| GET  | `/triage` | decisions awaiting doctor review |
| POST | `/triage/{id}/review` | confirm/override model decision |

### Quick test (no keys needed)
```bash
curl -X POST localhost:8000/calls/start \
  -H 'Content-Type: application/json' -d '{"patient_id":1}'

curl -X POST localhost:8000/calls/1/analyze \
  -H 'Content-Type: application/json' \
  -d '{"transcript":"Patient: my chest feels tight and I am dizzy."}'
```
With Ollama running, `/analyze` returns real triage (urgency + action).

## 3. Dashboard (Flutter)

```bash
cd dashboard
flutter pub get
flutter run -d chrome        # web
# or: flutter run             # mobile (change apiBase to your LAN IP)
```

The dashboard lists patients (high-risk first), opens a detail view
(vitals, meds, past reports = agent memory), and has a **Start check-in
call** button that calls `/calls/start`.

---

## Project layout
```
backend/app/
  main.py              FastAPI app + read endpoints
  routers_calls.py     call orchestration + triage review
  agent.py             MedGemma reasoning (question plan, analyze)
  medgemma.py          Ollama client (graceful fallback)
  channels/            swappable interface adapters (ElevenLabs...)
  models.py            SQLAlchemy data model
  seed.py              demo data
dashboard/lib/main.dart  Flutter doctor dashboard
```

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
