# Task: Wire ElevenLabs Agents + Twilio into the Guardian FastAPI backend

> Hand this file to Claude Code. Read `SCOPE.md` first for product context (Guardian = AI agent that phones discharged patients, runs a check-in interview, and reports to the doctor). This task covers **only the telephony/SMS integration layer**. The clinical logic (MedGemma) is stubbed behind interfaces.

## 0. Ground rules

- Backend is **Python / FastAPI + SQLAlchemy + Postgres**. Do not introduce other frameworks.
- Keep the **channel-agnostic core** principle from SCOPE.md: ElevenLabs and Twilio code lives in **adapter modules** (`app/adapters/`). Core services (triage, reports, scheduling) must not import `twilio` or call ElevenLabs directly.
- All secrets come from environment variables (`.env`, never committed). Provide `.env.example`.
- Every webhook endpoint must **verify the sender's signature** and return **HTTP 200 quickly**. Heavy work (MedGemma, PDF, email) goes to a background task.
- Add a `MOCK_TELEPHONY=true` toggle: when set, adapters log instead of calling ElevenLabs/Twilio, so the rest of the app can be developed and demoed offline.
- Do not hardcode phone numbers or keys. Use E.164 format (`+4915112345678`) everywhere.
- Work in small steps, run the app and tests after each step, and commit after each step.

## 1. Manual prerequisites (the human does these; Claude Code should NOT try to automate them)

Print this checklist back to the user at the start and wait for the values listed in section 2.

1. **Twilio**: buy a **voice + SMS capable** number (a purchased number is required for inbound calls; verified caller IDs are outbound-only). Create an **API Key** (SID starts with `SK`, plus secret).
   - Trial accounts usually only reach verified numbers. Note that country regulations differ (e.g. Germany needs regulatory paperwork), so a US number plus verified team phones is the safest hackathon setup.
2. **ElevenLabs**: create an agent in the ElevenAgents dashboard (see section 4 for the prompt and variables). Import the Twilio number under **Phone Numbers** (label, number, Twilio SID, Twilio token). ElevenLabs configures the number's voice settings automatically, so **do not edit the voice webhook in Twilio afterwards**.
3. Assign the agent to the imported number, then send a **test outbound call** from the dashboard to a personal phone. Confirm it works before any code is written.
4. Expose the local server with **ngrok** (or the DigitalOcean droplet URL) so ElevenLabs and Twilio can reach webhooks.

## 2. Environment variables (`.env.example`)

```
# General
PUBLIC_BASE_URL=https://<ngrok-or-droplet-host>
MOCK_TELEPHONY=false
DATABASE_URL=postgresql+psycopg://...

# ElevenLabs
ELEVEN_API_KEY=
ELEVEN_AGENT_ID=
ELEVEN_AGENT_PHONE_NUMBER_ID=      # from the imported Twilio number
ELEVEN_WEBHOOK_SECRET=             # post-call webhook HMAC secret
ELEVEN_TOOL_AUTH_TOKEN=            # shared secret the agent's webhook tools send to us
ELEVEN_BASE_URL=https://api.elevenlabs.io

# Twilio
TWILIO_ACCOUNT_SID=
TWILIO_API_KEY_SID=
TWILIO_API_KEY_SECRET=
TWILIO_AUTH_TOKEN=                 # used to validate inbound webhook signatures
TWILIO_NUMBER=+1...

# Scheduling
CALLBACK_DEFAULT_MINUTES=15        # use 1 for demos
```

## 3. Target file layout

```
app/
  config.py                      # pydantic-settings, reads env
  adapters/
    elevenlabs.py                # place_outbound_call(), verify_webhook()
    twilio_sms.py                # send_sms(), validate_twilio_request()
  routers/
    calls.py                     # POST /calls/start, GET /calls/{id}, POST /calls/{id}/callback
    eleven_webhooks.py           # POST /calls/webhook, POST /inbound/voice-init
    eleven_tools.py              # POST /tools/schedule_callback, POST /tools/flag_emergency
    inbound_sms.py               # POST /inbound/sms
    sms.py                       # POST /sms/send
  services/
    call_orchestrator.py         # start_call(), resume_call(), handle_post_call()
    scheduler.py                 # APScheduler: callbacks + reminders
    context_builder.py           # builds dynamic variables for a patient
    clinical.py                  # STUBS: build_question_plan(), generate_report(), triage()
  models/ ...                    # per SCOPE.md data model (call_sessions, call_reports, sms_log, ...)
tests/
  test_webhooks.py
  test_sms_inbound.py
  test_call_start.py
```

Reuse existing models from SCOPE.md. Add these columns to `call_sessions` if missing: `conversation_id`, `call_sid`, `callback_at`, `checkpoint` (JSON), `attempt` (int).

## 4. ElevenLabs agent configuration (document in `docs/agent_setup.md`, human applies it)

Generate a ready-to-paste file `docs/agent_setup.md` containing:

**System prompt (patient check-in persona)** using these dynamic variables, all of which must be defined on the agent:

| Variable | Meaning |
|---|---|
| `patient_name` | Patient's name |
| `question_plan` | Ordered questions from MedGemma (doctor-required plus history-aware follow-ups) |
| `last_call_summary` | Summary of the previous call, or "none" |
| `session_id` | Our `call_sessions.id`, used to correlate tool calls and webhooks |
| `resume_from` | Checkpoint text for callback calls, or "start" |

Prompt requirements:
- Introduce itself as an AI assistant calling on behalf of the patient's doctor.
- Ask the questions in `question_plan`, and skip questions already answered.
- If a vital (BP, blood sugar, weight) is unavailable, call the `schedule_callback` tool and tell the patient when it will call back.
- If the patient reports red flags (chest pain, difficulty breathing, severe distress, medication clearly not working), call `flag_emergency`, then tell the patient the doctor will be alerted. Never diagnose.
- If `resume_from` is not "start", continue from that point and do not repeat earlier questions.
- On repeat calls, briefly summarize `last_call_summary` and offer to rescheduled or extra calls.

**Webhook tools** (Tools tab, type Webhook), each with header `Authorization: Bearer {{secret__eleven_tool_token}}` (store the token as an ElevenLabs secret):
1. `schedule_callback` to `POST {PUBLIC_BASE_URL}/tools/schedule_callback` with body fields `session_id` (use `{{session_id}}`), `minutes` (number), `missing_item` (string), `checkpoint` (string, what was asked and answered so far).
2. `flag_emergency` to `POST {PUBLIC_BASE_URL}/tools/flag_emergency` with `session_id`, `reason`, `recommended_action` (enum: `doctor_callback | ambulance | request_appointment`).

Tool handlers must respond in well under 20 seconds (they only write DB rows; scheduler does the rest).

**Workspace settings**:
- Enable **post-call webhooks** pointing to `{PUBLIC_BASE_URL}/calls/webhook`, store the HMAC secret in `ELEVEN_WEBHOOK_SECRET`.
- Agent **Security** tab: enable "fetch conversation initiation data" for inbound Twilio calls, pointing to `{PUBLIC_BASE_URL}/inbound/voice-init`.

## 5. Implementation steps (do them in order; each must run and be tested before the next)

### Step 1: Config and adapters
- `config.py` with pydantic-settings for all variables above.
- `adapters/elevenlabs.py`:
  - `place_outbound_call(to_number, dynamic_variables) -> {conversation_id, call_sid}` calls `POST {ELEVEN_BASE_URL}/v1/convai/twilio/outbound-call` with header `xi-api-key` and JSON body:
    ```json
    {
      "agent_id": "...",
      "agent_phone_number_id": "...",
      "to_number": "+...",
      "conversation_initiation_client_data": {
        "dynamic_variables": { "patient_name": "...", "question_plan": "...",
                               "last_call_summary": "...", "session_id": "...",
                               "resume_from": "start" }
      }
    }
    ```
    The response contains `success`, `message`, `conversation_id`, `callSid`. Raise a clear error if `success` is false.
  - `verify_webhook(raw_body: bytes, signature_header: str)` validates the `elevenlabs-signature` header against `ELEVEN_WEBHOOK_SECRET`. Prefer the official `elevenlabs` Python SDK's webhook helper (`construct_event`); check the installed SDK version's docs for the exact call.
- `adapters/twilio_sms.py`:
  - `send_sms(to, body)` using the `twilio` SDK with API key auth (`Client(api_key_sid, api_key_secret, account_sid)`), log to `sms_log`.
  - `validate_twilio_request(url, form_params, signature)` using `twilio.request_validator.RequestValidator(TWILIO_AUTH_TOKEN)`.
- Honor `MOCK_TELEPHONY` in both adapters.

### Step 2: Outbound call start
- `POST /calls/start {patient_id}`:
  1. Create `call_sessions` row (`status=scheduled`, `direction=outbound`, `channel=voice`).
  2. `context_builder` loads patient + last N `call_reports`, calls `clinical.build_question_plan()` (stub returns the doctor's enabled questions joined as numbered text), builds the dynamic variables.
  3. Call `place_outbound_call`; store `conversation_id` and `call_sid`; set `status=in_progress`.
- Test against the real number with `MOCK_TELEPHONY=false` once, and with the mock on in unit tests.

### Step 3: Post-call webhook
- `POST /calls/webhook`:
  1. Read the **raw body**, verify the signature, return 401 on failure.
  2. Parse `type`:
     - `post_call_transcription`: read the transcript, analysis and `conversation_initiation_client_data` (it echoes our dynamic variables, so recover `session_id` from it, falling back to `conversation_id` lookup). Save a `transcriptions` row, then enqueue `handle_post_call(session_id)` as a **background task** (stub: `generate_report`, `triage`, update vitals, PDF, email). Mark the session `completed`.
     - `call_initiation_failure`: failure to connect, declined, or not answered. Mark the session `missed`, send the "missed call" SMS with the next call time, and re-schedule.
     - `post_call_audio`: ignore (log only).
  3. Always return `{"status":"received"}` with HTTP 200 for valid, verified events, even if background work later fails (log the error). Non-200 responses count as failures and trigger retries, so make handling **idempotent** (dedupe on `conversation_id`).
- Note: voicemail is treated as a successfully initiated call, so also add a check in `handle_post_call` that flags transcripts with no real patient responses as `missed`.

### Step 4: Mid-call tools
- `POST /tools/schedule_callback`: verify `Authorization: Bearer ELEVEN_TOOL_AUTH_TOKEN`. Set `call_sessions.status=callback_pending`, `callback_at = now + minutes` (default `CALLBACK_DEFAULT_MINUTES`), `checkpoint = {...}`. Register a scheduler job. Send SMS "Guardian will call you back at HH:MM to record your <missing_item>." Return a short JSON the agent can speak from (`{"ok":true,"callback_time":"14:45"}`).
- `POST /tools/flag_emergency`: same auth. Create a `triage_decisions` row (`urgency=urgent|emergency`, action from the tool), mark the session `escalated`, notify the doctor (email stub). Return `{"ok":true}`.

### Step 5: Scheduler and callback resume
- `services/scheduler.py` with APScheduler (persist jobs in Postgres via SQLAlchemyJobStore so restarts do not lose them).
- Job `resume_call(session_id)`: places a new outbound call with `resume_from=<checkpoint>` and the same context; increment `attempt`; cap attempts at 3, then SMS the patient and flag the doctor.
- On startup, re-load any `callback_pending` sessions whose `callback_at` is in the future.

### Step 6: Inbound voice (patient calls the agent)
- `POST /inbound/voice-init` receives `caller_id`, `agent_id`, `called_number`, `call_sid` (ElevenLabs calls this when a Twilio call arrives). Look up the patient by `caller_id`. Return JSON:
  ```json
  { "type": "conversation_initiation_client_data",
    "dynamic_variables": { "patient_name": "...", "question_plan": "...",
                           "last_call_summary": "...", "session_id": "...", "resume_from": "start" } }
  ```
  Every dynamic variable defined on the agent must be present. Unknown caller gets safe defaults (`patient_name="caller"`, `question_plan="Ask how you can help and note their name and issue."`). Create an `inbound` `call_sessions` row. Keep this endpoint **fast** since it delays call pickup.
- Do not try to send `system__*` variables, because those cannot be overridden.

### Step 7: Inbound SMS
- In the Twilio console, the human sets the number's **Messaging webhook** to `POST {PUBLIC_BASE_URL}/inbound/sms`.
- `POST /inbound/sms` (form-encoded): validate `X-Twilio-Signature`, read `From`, `Body`. Look up the patient by phone.
  - `call` (case-insensitive): start a call via `call_orchestrator.start_call`, reply "Calling you now."
  - A time such as `18:00` or `in 2 hours`: parse, set the next call time, confirm by SMS.
  - Anything else: log, reply with a short help text, and flag for the doctor if it looks urgent.
  - Return an empty TwiML `<Response/>` with `Content-Type: application/xml`.
- `POST /sms/send` for internal use (reminders, tips, photo links).

### Step 8: Tests
- Unit-test webhook signature rejection and acceptance (use a fixture that signs a body with a test secret).
- Test idempotency (same webhook delivered twice creates one report job).
- Test `schedule_callback` creates a scheduler job and an SMS log row.
- Test inbound SMS `call` triggers `start_call` (mocked).
- `MOCK_TELEPHONY=true` end-to-end smoke test: start call, simulate post-call webhook, assert report job queued.

## 6. Acceptance checklist (report each as pass/fail with evidence)

- [ ] `POST /calls/start` places a real call to the developer's phone and stores `conversation_id`.
- [ ] After hanging up, `/calls/webhook` receives and verifies the post-call event, and a `transcriptions` row exists.
- [ ] Telling the agent "I haven't taken my blood pressure" triggers `schedule_callback`, sends an SMS, and the phone rings again at the configured interval, resuming from the checkpoint.
- [ ] Not answering a call results in a `missed` session plus an SMS with the next call time.
- [ ] Texting `call` to the Twilio number triggers an outbound call.
- [ ] Calling the Twilio number reaches the agent with the correct patient name (inbound personalization).
- [ ] Invalid or unsigned webhook requests are rejected with 401/403.
- [ ] No secrets in git; `.env.example` is complete.

## 7. Known gotchas (read before debugging)

- Webhooks must return **200** or ElevenLabs will retry, so keep handlers idempotent.
- Do not modify the imported number's voice configuration in the Twilio console. ElevenLabs manages it.
- Dynamic variable names must match the agent config exactly, and all defined variables must be supplied or the conversation initiation can fail.
- Twilio trial accounts: verified destination numbers only, and trial prefixes on SMS.
- Country-specific rules for numbers and SMS sender IDs vary, so verify in the Twilio console if messages are not delivered.
- If local webhooks do not arrive, check that the ngrok URL in ElevenLabs/Twilio matches `PUBLIC_BASE_URL` (it changes on every ngrok restart on free plans).
- Verify exact SDK method names against the installed `elevenlabs` and `twilio` package versions. Docs: https://elevenlabs.io/docs/eleven-agents/overview and https://www.twilio.com/docs.

## 8. Out of scope for this task

MedGemma prompts, PDF/email generation, dashboard UI, photo uploads (Phase 2). Leave clearly marked stubs with `# TODO(clinical)` and the function signatures listed in section 3.
