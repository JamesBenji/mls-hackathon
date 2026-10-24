# Task: Deploy MedGemma + Gemma 4 on a DigitalOcean GPU Droplet and plug them into Guardian

> Hand this file to Claude Code together with `SCOPE.md` (product context) and `ELEVENLABS_TWILIO_SETUP.md` (telephony). This task builds the **clinical brain**: the model server on DigitalOcean plus the Python module that replaces the `# TODO(clinical)` stubs in `app/services/clinical.py`.

## 0. Ground rules

- **Models:** MedGemma (clinical reasoning, and vision in Phase 2) and **Gemma 4** (strict structured output) both run through **Ollama** on one DigitalOcean GPU Droplet. Model names are **config, not constants** (see section 2), because tags change.
- **Do NOT expose Ollama to the internet.** Ollama has no authentication. Patient-style data flows through it. Keep it bound to `127.0.0.1`, put FastAPI (with an API key) in front, and let teammates reach Ollama only through an SSH tunnel. Do not set `OLLAMA_ORIGINS=*`, do not open port 11434 in the firewall.
- **Fake data only.** This is a hackathon. Never load real patient data. Seed data per `SCOPE.md` is fictional.
- **The model never has the final word.** Every triage or report is a *draft for the doctor* (`doctor_reviewed=false`). Add a deterministic red-flag keyword check that runs independently of the LLM (section 6.4).
- **Latency budget:** anything on the live-call path (`/calls/start` question plan) must finish in **≤ 8 seconds** or fall back to the doctor's static questions. Heavy reasoning (report, SOAP, triage) runs after the call in a background task.
- All secrets in `.env` (never committed); provide `.env.example`.
- Work in small steps, run and test after each step, commit after each step.

## 1. Manual prerequisites (the human does these; Claude Code prints this checklist and waits)

1. **Create the GPU Droplet** in the DigitalOcean control panel: Create, then GPU Droplets. Pick the **AI/ML-ready image** (NVIDIA drivers preinstalled; it is Ubuntu 22.04-based) or the inference-optimized image (Ubuntu 24.04, CUDA 12.9). Add your SSH key.
2. **Pick the GPU by which models you want resident at once:**

   | GPU | VRAM | ~Price/hr | Fits |
   |---|---|---|---|
   | RTX 4000 Ada | 20 GB | ~$0.76 | `medgemma:4b` + `gemma4:12b` (recommended start) |
   | L40S | 48 GB | ~$1.57 | the above plus `medgemma:27b` and/or `gemma4:26b`/`31b` |
   | H100 | 80 GB | ~$3.39 | everything, but overkill for the hackathon |

   Prices are from DigitalOcean's published on-demand rates and may change. With 250 EUR of credit an L40S is roughly 150 hours of runtime, so **destroy the droplet when the hackathon ends** (a powered-off droplet typically still bills; snapshot it first if you want to keep the setup).
3. Create a **DigitalOcean Cloud Firewall** (or use `ufw` later) allowing inbound **22, 80, 443 only**. Never 11434.
4. Give Claude Code the droplet IP (and optionally a DNS name). If there is no domain, use `<ip-with-dashes>.sslip.io` (for example `203-0-113-10.sslip.io`), which lets Caddy obtain a real HTTPS certificate with zero DNS work.

## 2. Environment variables (`.env.example`)

```
# Model server (FastAPI talks to Ollama on the same droplet)
OLLAMA_URL=http://127.0.0.1:11434
MEDGEMMA_MODEL=medgemma:4b          # verify tag, see section 4
GEMMA4_MODEL=gemma4:12b             # structured JSON + final text
CLINICAL_PIPELINE=two_stage         # two_stage | single
MODEL_TIMEOUT_S=120
PLAN_TIMEOUT_S=8                    # live-call path budget
OLLAMA_NUM_CTX=16384                # explicit, see gotchas

# API protection for teammates / frontend
GUARDIAN_API_KEY=                   # required header X-API-Key on all non-webhook routes
PUBLIC_HOST=203-0-113-10.sslip.io
```

## 3. Droplet bootstrap (`infra/bootstrap_droplet.sh`, idempotent, run over SSH)

Generate this script; the human runs it on the droplet. It must:

1. `nvidia-smi` and exit with a clear message if no GPU is visible.
2. Install Ollama: `curl -fsSL https://ollama.com/install.sh | sh`. Check `ollama --version` and upgrade if older than the minimum for Gemma 4 (reported as 0.22 or newer; confirm in Ollama's release notes).
3. Configure the systemd override, **loopback only**:
   ```
   # /etc/systemd/system/ollama.service.d/override.conf
   [Service]
   Environment="OLLAMA_HOST=127.0.0.1:11434"
   Environment="OLLAMA_KEEP_ALIVE=24h"
   Environment="OLLAMA_MAX_LOADED_MODELS=2"
   Environment="OLLAMA_NUM_PARALLEL=2"
   Environment="OLLAMA_CONTEXT_LENGTH=16384"
   ```
   then `systemctl daemon-reload && systemctl restart ollama`.
4. `ufw default deny incoming; ufw allow 22,80,443/tcp; ufw --force enable`. Do **not** allow 11434 or 8000.
5. Install Caddy and write `/etc/caddy/Caddyfile`:
   ```
   {$PUBLIC_HOST} {
       reverse_proxy 127.0.0.1:8000
   }
   ```
6. Create a systemd unit `guardian-api.service` running `uvicorn app.main:app --host 127.0.0.1 --port 8000` from the repo with the project's virtualenv and `EnvironmentFile=/opt/guardian/.env`. FastAPI binds to loopback; Caddy is the only public door.
7. Print a summary of what was configured and the verification commands from section 4.

Teammates who need direct Ollama access use a tunnel: `ssh -L 11434:127.0.0.1:11434 root@<droplet-ip>`.

## 4. Model pulls and verification (`infra/pull_models.sh`)

Model tags change, so the script **verifies before it assumes**:

1. `ollama pull gemma4:12b`. Gemma 4 tags reported on Ollama include `e2b`, `e4b`, `12b`, `26b` and `31b`, and a bare `gemma4` resolves to the small E4B variant, so always use an explicit tag. QAT builds such as `gemma4:12b-it-qat` are smaller on disk if VRAM is tight.
2. `ollama pull medgemma:4b` (multimodal, ~3.3 GB). If the tag does not exist or errors:
   - Check https://ollama.com/library for the current MedGemma tag, or
   - Fallback A: import a community GGUF of `google/medgemma-4b-it` via a `Modelfile` (`FROM ./medgemma-4b-it.gguf`, then `ollama create medgemma-local -f Modelfile`). The Hugging Face repo requires accepting Google's license and an HF token, so this needs the human.
   - Fallback B (per `SCOPE.md` risks table): run `gemma4:12b` alone for everything and set `CLINICAL_PIPELINE=single`. Record the fallback in the README.
   - Update `.env` with whatever tag actually works.
3. Optional on L40S/H100: `ollama pull medgemma:27b` (reported text-only on Ollama; verify) for deeper reasoning.
4. **Warm-up:** call each model once with `keep_alive: "24h"` so the first real request is not a cold load.
5. Verify and print `ollama list` and `ollama ps` (check the `PROCESSOR` column says `100% GPU`, not a CPU split).
6. Smoke test both models with a tiny prompt through `curl http://127.0.0.1:11434/api/chat`, printing latency.

## 5. Code: model gateway (`app/adapters/ollama_client.py`)

A thin async wrapper, no clinical logic inside.

```python
async def chat(model: str, messages: list[dict], *, schema: dict | None = None,
               temperature: float = 0.2, timeout: float | None = None,
               images_b64: list[str] | None = None) -> str:
    """POST {OLLAMA_URL}/api/chat, stream=False.
    - schema: pass a JSON Schema (Pydantic .model_json_schema()) as `format`
              so output is constrained; otherwise free text.
    - options: {"temperature": t, "num_ctx": settings.OLLAMA_NUM_CTX}
    - keep_alive: "24h"
    - images_b64 attached to the last user message's "images" field (Phase 2 vision).
    Raise ModelUnavailable on connection errors/timeouts so callers can fall back."""

async def chat_json(model, messages, schema_model: type[BaseModel], **kw) -> BaseModel:
    """Calls chat(..., schema=schema_model.model_json_schema(), temperature=0),
    validates with Pydantic, retries ONCE with the validation error appended to the prompt."""
```

Add `GET /health/models` (API key required) that reports, per configured model: loaded or not, round-trip latency of a 5-token prompt, and GPU/CPU placement from `GET /api/ps`.

## 6. Code: clinical service (`app/services/clinical.py`) replacing the stubs

Keep the function signatures already used by the telephony task. All outputs are validated Pydantic models (`app/schemas/clinical.py`).

### 6.1 Schemas

```python
class PlannedQuestion(BaseModel):
    id: str
    text: str
    source: Literal["doctor", "history_followup", "redflag_probe"]
    skip_if_answered: bool = True

class QuestionPlan(BaseModel):
    questions: list[PlannedQuestion]

class Soap(BaseModel):
    subjective: str; objective: str; assessment: str; plan: str

class CapturedData(BaseModel):
    bp_systolic: int | None = None
    bp_diastolic: int | None = None
    blood_sugar: float | None = None
    weight_kg: float | None = None
    sleep_hours: float | None = None
    meds_taken: bool | None = None
    med_time: str | None = None
    side_effects: list[str] = []
    symptoms: list[str] = []
    missing_items: list[str] = []      # e.g. ["blood_pressure"] -> callback candidates

class CallReportDraft(BaseModel):
    summary: str                        # <= 3 sentences, doctor-facing
    soap: Soap
    captured: CapturedData
    risk_flags: list[str]
    changes_since_last_call: list[str]

class TriageResult(BaseModel):
    urgency: Literal["none", "monitor", "urgent", "emergency"]
    recommended_action: Literal["none", "doctor_callback", "ambulance", "request_appointment"]
    rationale: str
    uncertain_because: list[str] = []   # what data the model lacked

class CallJobDraft(BaseModel):          # doctor's personal agent
    patient_ref: str                    # name or id as spoken/typed
    situation: str
    treatment: str
    questions: list[str]
    scheduled_at: str | None            # ISO 8601 in Europe/Berlin, or null if unspecified
```

### 6.2 `build_question_plan(patient, last_reports, doctor_questions) -> QuestionPlan`

- Input: patient record, last N (default 3) `call_reports` summaries, vitals trend (last 7 days), the doctor-enabled questions from the question bank in `SCOPE.md`.
- Use `GEMMA4_MODEL` with `chat_json`, temperature 0, wrapped in `asyncio.wait_for(..., PLAN_TIMEOUT_S)`.
- Rules in the system prompt: keep **all doctor-required questions**; add at most 3 history-aware follow-ups (e.g. cough last week, BP high last call); always include a red-flag probe (chest pain, breathing difficulty, severe dizziness); if the new prescription flag is true, probe side effects, otherwise ask about improvement; never include diagnostic statements.
- **Fallback on timeout, error or invalid JSON:** return the doctor's static questions in order. Log a warning. The call must never fail because the model was slow.
- Helper `render_plan_for_agent(plan) -> str` produces the numbered text injected into the ElevenLabs `question_plan` dynamic variable.

### 6.3 `generate_report(transcript, patient, last_reports) -> CallReportDraft` (post-call, background)

- `two_stage`: (1) `MEDGEMMA_MODEL` free-text clinical analysis of the transcript against history (what changed, concerns, medication issues); (2) `GEMMA4_MODEL` `chat_json` turns that analysis plus the raw transcript into `CallReportDraft`. `single`: one Gemma 4 pass.
- Extraction must only record values the **patient actually stated**. Never infer or invent numbers (a missing BP is `null` plus an entry in `missing_items`).
- Persist to `call_reports`, then write `captured` values into `vitals` (`source="call"`), update `patient.risk_level` suggestions (not auto-applied; store as proposal).

### 6.4 `triage(report, transcript, patient) -> TriageResult`

- Stage 1: **deterministic red-flag scan** (`app/services/redflags.py`), a list of case-insensitive patterns in English and German (chest pain, cannot breathe / short of breath, fainted, stroke signs such as facial droop or slurred speech, severe bleeding, suicidal statements, plus BP above 180/120 or blood sugar below 54 mg/dL). Any hit sets a **minimum urgency of `urgent`** that the LLM cannot lower. Thresholds are placeholders to be confirmed by a clinician and live in config.
- Stage 2: LLM classification via `chat_json` for urgency and recommended action. Final urgency is `max(rule_based, llm)`.
- Persist to `triage_decisions` with `doctor_reviewed=false`. State in `uncertain_because` what the model could not know (no full chart, no exam).
- Never produce patient-facing diagnoses or medication changes. Anything said to the patient comes from the ElevenLabs agent prompt, not from this module.

### 6.5 `extract_call_job(text, doctors_patients) -> CallJobDraft` (doctor's personal agent)

- Input is a typed message or a speech-to-text transcript from the doctor. Resolve `patient_ref` against the doctor's own patient list with fuzzy matching; if ambiguous, return a clarification question instead of guessing.
- Use Gemma 4 `chat_json`. Resolve relative times ("tomorrow at 9") using the current time passed in explicitly (Europe/Berlin), never the model's idea of the date.

### 6.6 Phase 2 stub: `compare_images(prior_b64, new_b64, context)`

Leave a documented stub using `medgemma:4b` with `images_b64` (recovery comparison between two uploads). Do not build UI for it now.

## 7. Wiring and background work

- `call_orchestrator.start_call` calls `build_question_plan` (with the 8 s fallback) before `place_outbound_call`.
- `handle_post_call` runs `generate_report`, then `triage`, then vitals write-back, then notification (email and PDF are separate tasks). Wrap each step so one failure does not lose the transcript, and mark the report `status=partial` with the error stored.
- Serialize model access sensibly: do not fire more than `OLLAMA_NUM_PARALLEL` concurrent generations; use an `asyncio.Semaphore`.

## 8. Tests

- Unit tests for schemas and `chat_json` retry logic using a fake Ollama (`httpx.MockTransport`).
- `build_question_plan` returns the static fallback when the model times out.
- Red-flag scanner: parametrized test with positive and negative phrases (English and German), and a test that the LLM result cannot downgrade a rule hit.
- Report extraction on 3 fixture transcripts (BP given, BP missing, red-flag symptom). Assert that `missing_items` is populated rather than numbers invented.
- Integration test (marked `@pytest.mark.live`, skipped by default) hitting the real droplet.

## 9. Acceptance checklist (report pass/fail with evidence)

- [ ] `nvidia-smi` shows the GPU and `ollama ps` shows both models on **100% GPU**.
- [ ] Port 11434 is **not** reachable from the internet (`nc -zv <ip> 11434` fails from outside); `https://<PUBLIC_HOST>/health/models` works with the API key and is rejected without it.
- [ ] `build_question_plan` returns valid JSON in under 8 s warm, and the fallback path is exercised in a test.
- [ ] A fixture transcript produces a `CallReportDraft`, `vitals` rows, and a `triage_decisions` row with `doctor_reviewed=false`.
- [ ] A transcript containing "chest pain" yields at least `urgent` even if the LLM says `none`.
- [ ] Missing BP in a transcript yields `missing_items=["blood_pressure"]` and no invented values.
- [ ] Repo contains `.env.example`, `infra/bootstrap_droplet.sh`, `infra/pull_models.sh`, README section on teardown and cost.

## 10. Known gotchas

- **Context window:** reports say Ollama defaults Gemma 4 to a **4K** context even though the model supports far more, which silently truncates long transcripts and history. Always pass `num_ctx` explicitly (this task uses 16384) and watch VRAM, since a larger context costs memory.
- A bare `gemma4` tag is the small E4B model. Use explicit tags.
- First request after a restart is slow (model load). Run the warm-up step before demos and keep `OLLAMA_KEEP_ALIVE=24h`.
- If `ollama ps` shows a CPU/GPU split, the model does not fit; drop to a smaller tag or QAT build.
- JSON-schema-constrained output is far more reliable than "return JSON" prompting; use `format=<schema>`.
- Droplets keep billing until destroyed. Set a calendar reminder.
- Webhook-facing routes (ElevenLabs, Twilio) authenticate by signature, not by `X-API-Key`; make sure the API-key middleware excludes them.

## 11. Explicitly out of scope (ignore from the pasted blueprint)

The other blueprint's ElevenLabs **voice cloning** endpoint, **TTS discharge audio**, and **React dashboard** are not part of Guardian. Guardian's voice runs through the ElevenLabs conversational agent (see `ELEVENLABS_TWILIO_SETUP.md`), and the dashboard is Flutter per `SCOPE.md`. Also do not copy its `allow_origins=["*"]`, open `11434`/`8000` firewall rules, or `OLLAMA_ORIGINS=*`.
