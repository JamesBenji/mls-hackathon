# Coolify deployment (planned, not deployed)

## Backend dependency — merge before deploying

This infrastructure-only PR targets main but expects the backend from
`feat/medgemma-elevenlabs` plus [backend reconciliation PR #4](https://github.com/jamesbenji/mls-hackathon/pull/4)
(`feat/reconcile-followup-plans`). Do not deploy
the older main backend with this image. No backend source is changed here.
The reconciliation branch supplies `scripts.setup_followups`; confirm it is
included in the selected deployment revision before following schema steps below.
The authoritative rollout contract is `backend/FOLLOW_UP_CONTRACT.md` from PR #4.

## Resource setup

1. Create a **Docker Compose** resource from this repository, repository root `/`,
   Compose location `/compose.coolify.yaml`, and the merged deployment branch.
   Build context is the repository root; Dockerfile is `backend/Dockerfile`.
2. Add variables from `infra/coolify/.env.example` in Coolify's resource environment.
   Generate **independent random** `GUARDIAN_API_KEY` and `POSTGRES_PASSWORD`, e.g.
   `openssl rand -hex 32` on your own machine. Keep them secret. Compose rejects
   empty values; API startup rejects secrets shorter than 32 characters or containing
   whitespace. Placeholders are not secure secrets. Coolify's generated password
   feature is also suitable if configured for at least 32 random characters.
3. Assign API domain **https://medicall.nulkode.dev**, routing to container port
   **8000** in Coolify's domain/port settings. Point DNS at your server first.
   Coolify manages reverse proxy and HTTPS; do not add Caddy, a TLS container,
   host port mappings, or a public Postgres domain. Postgres lives only on the
   private internal database network. API also has an egress/proxy network.
4. Set `PUBLIC_BASE_URL` to that exact HTTPS origin (signature verification relies
   on it). Set exact browser origins in `CORS_ORIGINS`; localhost regex is disabled.
   Keep **one API replica, one uvicorn worker**, with no rolling overlap between
   old/new instances: APScheduler runs inside the API and shares Postgres state.
5. Keep `MOCK_TELEPHONY=true` and `PHOTO_PUSH_ENABLED=false` initially. No startup
   command seeds demo data, provisions an agent, or initiates calls. Existing due
   scheduler jobs can resume on startup; review restored jobs before enabling real
   telephony. A Compose healthcheck failure alone does not restart a live process;
   use Coolify monitoring/alerts. Restart policy covers process exit/server restart.

## Integrations and secrets

The production Compose mounts `/opt/medicall/secrets/elevenlabs-webhook-secret` read-only at `/run/secrets/elevenlabs-webhook-secret` and sets `ELEVEN_WEBHOOK_SECRET_FILE`. The file contains only the HMAC signing secret issued by the workspace webhook configured for `https://medicall.nulkode.dev/calls/webhook`; owner 10001:10001, mode 0400. It is already installed on the Medicall server. For another server, provision the file before deployment or change `ELEVEN_WEBHOOK_SECRET_HOST_PATH`. When configured, the file takes precedence over `ELEVEN_WEBHOOK_SECRET`; unreadable or missing credentials fail closed. Never copy the secret into this repository or support logs.

Guardian Main contains the merged plan-driven photo workflow and the signed post-call webhook. Leave `ELEVEN_IMMEDIATE_CALL_BRANCH_ID` empty (remove any old staged branch value) to use Main. Explicit branch overrides remain available for staging. Calls use the normal agent duration unless the API explicitly requests an override. Set `MOCK_TELEPHONY=false` only when real calls are intended; this also affects approved scheduled calls.

- Cloud Gemma: server-only `GOOGLE_API_KEY`, `GEMMA_MODEL=gemma-4-31b-it`.
  No Ollama, local model downloads, or GPU service is needed.
- ElevenLabs: `ELEVEN_API_KEY`, `ELEVEN_WEBHOOK_SECRET`, and an independent strong
  `ELEVEN_TOOL_AUTH_TOKEN`. Existing agent/phone IDs in the example are public
  identifiers, **not credentials**. Before deliberately setting mock mode false,
  verify these refer to the intended account, configure HTTPS callback URLs and
  webhook authentication manually, and review consent/phone destinations.
- SMS is optional: configure `TWILIO_ACCOUNT_SID`, `TWILIO_API_KEY_SID`,
  `TWILIO_API_KEY_SECRET`, `TWILIO_AUTH_TOKEN`, `TWILIO_NUMBER` if needed.
- Optional email variables are exposed in Compose (`GMAIL_ADDRESS`,
  `GMAIL_APP_PASSWORD`, `EMAIL_FROM`, `DOCTOR_ALERT_EMAIL`).
- Never put service credentials in dashboard/patient builds. Keep the shared
  `GUARDIAN_API_KEY` server-side (e.g. a trusted dashboard proxy); patient photo
  clients use the backend's separately issued scoped tokens, not this shared key.
  This shared key is **service authentication, not multi-tenant doctor identity**;
  it does not stop its holder from selecting another doctor ID.

### Optional Firebase ADC file

Default deployment needs no Firebase key. To enable photo push, put the real ADC
JSON on the **server**, outside the repository, e.g.
`/opt/medicall/secrets/firebase-service-account.json`. Limit access to container
UID/GID **10001:10001** (e.g. owner 10001 and mode 0400). Never paste the key into
Git, build arguments, logs, or a public volume. Use Coolify's read-only file mount
to `/run/secrets/firebase-service-account.json` and add API environment
`GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/firebase-service-account.json`.

Alternatively merge the supplied override using the resource's supported multiple
Compose-file configuration:

```sh
docker compose -f compose.coolify.yaml -f infra/coolify/firebase.compose.yaml config --quiet
```

Set `FIREBASE_CREDENTIALS_HOST_PATH` to the **absolute host file path**. The override
bind-mounts it read-only and refuses to create a missing source directory. Use
either the override or Coolify's mount, not both. Only then deliberately enable
`PHOTO_PUSH_ENABLED=true`. No base64 bootstrap or privileged startup is needed.

## Schema and first start

Back up an existing database, private photos and reports before upgrading. Stop
the old API/scheduler first, including any previous deployment outside this resource.
With the reconciled backend image built and Postgres healthy, run these one-off commands in the same Compose resource
environment (Coolify terminal/host Compose context):

```sh
docker compose -f compose.coolify.yaml stop api
docker compose -f compose.coolify.yaml build api
docker compose -f compose.coolify.yaml up -d postgres
docker compose -f compose.coolify.yaml run --rm --no-deps api python -m scripts.setup_followups
docker compose -f compose.coolify.yaml run --rm --no-deps api python -m scripts.setup_followups --apply
```

Run the read-only preflight first; proceed with `--apply` only on success. It
creates missing tables without resetting clinical data. **STOP on missing columns:**
`Base.metadata.create_all()` does not add columns to existing tables (including
critical/follow-up flags). Have reviewed, additive SQL migrations applied before
starting the new API; the preflight intentionally refuses to guess defaults.
On a brand-new empty database the same `--apply` initializes the shared schema.
Do not run these commands until `setup_followups` is present. Never run
`python -m app.seed` in production: it resets the schema. No migration or seed
is automatically run by this image; ordinary app startup only creates missing tables.
The image working directory `/app` is the equivalent of running the Python commands
from `backend/` locally. Review/remove old `job:*` scheduler entries from the previous
automatic extraction flow before restarting; those lacked explicit doctor approval.
Do not run provisioning scripts: the live agent/tools/model/voice/phone configuration
is authoritative. Review staged photo-tool runtime/header bindings manually.
Photo-quality work is an in-process asynchronous task, not a separate worker service;
interrupted checks become failed when polled after five minutes and allow retakes.
The inherited doctor notification hook is not a guaranteed delivered clinical alert.

After preflight/apply, deploy API normally. `/health` is public **liveness only**;
it does not verify model credentials or database readiness. Postgres startup uses
`pg_isready`. Check protected `/health/models` manually through trusted server-side
tooling after configuring the cloud key; do not put secrets in URLs.

## Persistence and operations

Named volumes retain Postgres, private photos at `/var/lib/guardian/private-photos`,
and PDFs at `/app/app/reports_out` (the path used by `app/services/pdf.py`). New
application volumes inherit owner 10001 and private permissions from the image.
For restored/existing volumes fix ownership to 10001:10001 before starting;
do not run the API as root to work around permissions. Never publicly serve either
file directory. Back up **all three volumes together**, encrypt backups, restrict
access and test restoration. Keep the same Coolify resource/project identity to
reuse volume names. Do not run `docker compose down -v`.

Changing `POSTGRES_PASSWORD` in Coolify does **not** change the role password in an
already initialized database: rotate the database role and resource secret in a
coordinated maintenance window. Avoid zero-downtime replicas/parallel schedulers.
Timezone defaults to `Europe/Berlin` and is configurable via `TIMEZONE`.

Local config validation (no deployment or calls) requires disposable environment
values for the two required variables, then:

```sh
docker compose -f compose.coolify.yaml config --quiet
```

Avoid printing expanded `docker compose config` in CI/support logs: it contains
secrets. No actual deployment, DNS changes, key provisioning or server changes
are performed by these files.
