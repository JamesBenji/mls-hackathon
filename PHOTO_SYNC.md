# Photo sync: backend + Flutter contract

Backend only; dashboard styling/UI belongs to the separate dashboard workstream. No voice or scheduling changes.

**Security limitation:** existing clinical/patient/vitals/triage endpoints remain unauthenticated for colleague compatibility. Use synthetic data and put legacy routes behind a trusted-network/access gateway; do not expose the whole API publicly with patient data. All new routes below require opaque bearer credentials and enforce current doctor/patient ownership. A patient ID is never authentication.

## Setup

From `backend/`, install dependencies, configure `backend/.env` from `.env.example`, and point `DATABASE_URL` at PostgreSQL using `postgresql+psycopg://...`:

```bash
python -m pip install -r requirements.txt
python -m app.photos.admin init-schema
python -m app.photos.admin issue-doctor 1
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`init-schema` only creates missing tables (`create_all(checkfirst=True)`); it never drops data or alters clinical columns. New tables: `photo_access_credentials`, `photo_patient_devices`, `photo_requests`, `photo_assets`. Assets link to existing `photo_uploads`; new legacy upload records have `source=patient_app` and no public URL/token. Run setup once before deploying; back up the DB first. Rollback leaves additive tables intact. **Do not run `app.seed` on an existing DB: it drops clinical tables.** An existing doctor/patient relationship must be provisioned through trusted administration first; only a disposable demo DB should be seeded before setup.

The CLI prints a new doctor bearer secret once plus its non-secret credential ID and expiry. Transfer securely to the doctor, never put it in source/build defines/URLs/logs. Revoke with `python -m app.photos.admin revoke CREDENTIAL_ID`. Credentials are random, stored hashed, expire in 90 days by default, and are not a production login/refresh system.

| Server setting | Default |
| --- | --- |
| `PHOTO_STORAGE_DIR` | `./private-photos` (use an absolute persistent private directory in deployment) |
| `PHOTO_MAX_BYTES` | `8388608` (8 MiB original and normalized image limit) |
| `PHOTO_MAX_PIXELS` | `20000000` |
| `PHOTO_CREDENTIAL_DAYS` | `90` |
| `PHOTO_PUSH_ENABLED` | `false` (requests still work by polling) |

For FCM, enable Cloud Messaging API in the Flutter app's Firebase project, grant a server service account messaging permission, set `PHOTO_PUSH_ENABLED=true`, and export `GOOGLE_APPLICATION_CREDENTIALS=/secure/path/service-account.json` in the **server process environment**, or use workload Application Default Credentials. Setting the ADC path only in Pydantic's `.env` is not sufficient. Never ship server credentials in Flutter. Configure app-side Firebase/notification permissions separately.

Require HTTPS outside localhost. Configure a proxy body limit of 9 MiB for default image limits, rate limits, and trusted dashboard CORS origins. Multipart spooling occurs before application file validation. Keep the image directory outside any static mount, with service-account-only filesystem ACLs (new POSIX directories 0700/files 0600; configure Windows ACLs explicitly), encrypted disks/backups, and persistent storage shared between API instances, or use one instance.

## Pairing + curl

The doctor verifies the patient in person/out of band, calls the owning-patient pairing endpoint, and securely transfers the returned patient bearer credential once (for example via a verified QR encounter). No public self-registration or arbitrary-ID login. Store the patient credential in OS-backed secure storage; never use the doctor secret in the patient app. Calling pairing again issues an additional credential; DELETE pairing revokes all patient credentials and clears all device registrations.

```bash
export BASE=http://localhost:8000
read -rs -p 'Doctor credential: ' DOCTOR_TOKEN; echo
curl -sS -X POST "$BASE/patients/1/pairing" -H "Authorization: Bearer $DOCTOR_TOKEN"
# Securely transfer access_token from that response to the verified patient.
read -rs -p 'Patient credential: ' PATIENT_TOKEN; echo
curl -sS "$BASE/me" -H "Authorization: Bearer $PATIENT_TOKEN"
curl -sS -X PUT "$BASE/me/devices/install-uuid" \
  -H "Authorization: Bearer $PATIENT_TOKEN" -H 'Content-Type: application/json' \
  -d '{"platform":"android","fcm_token":"FCM_TOKEN_FROM_SDK"}'
curl -sS -X POST "$BASE/patients/1/photo-requests" \
  -H "Authorization: Bearer $DOCTOR_TOKEN" -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: request-operation-uuid' -d '{"instructions":"Take a clear photo as discussed."}'
curl -sS "$BASE/me/photo-requests" -H "Authorization: Bearer $PATIENT_TOKEN"
export REQUEST_ID='UUID_FROM_REQUEST_RESPONSE'
curl -sS -X POST "$BASE/me/photo-requests/$REQUEST_ID/photos" \
  -H "Authorization: Bearer $PATIENT_TOKEN" -H 'Idempotency-Key: upload-operation-uuid' \
  -F 'file=@./photo.png;type=image/png'
curl -sS "$BASE/patients/1/photos" -H "Authorization: Bearer $DOCTOR_TOKEN"
curl -sS "$BASE/photos/PHOTO_UUID/content" -H "Authorization: Bearer $DOCTOR_TOKEN" -o review.jpg
curl -sS -X DELETE "$BASE/patients/1/pairing" -H "Authorization: Bearer $DOCTOR_TOKEN"
```

Examples use Bash; use `curl.exe` and your shell's variable syntax on Windows. Do not record expanded credentials in transcripts.

## Exact integration contract

All new calls require `Authorization: Bearer <credential>`. Paths have no `/api` prefix. Patient IDs are integers; request/photo IDs are UUID strings. Datetimes are UTC ISO-8601 strings currently without a timezone suffix (interpret as UTC explicitly in Dart). Lists are top-level JSON arrays; no pagination in this hackathon slice.

| Method/path | Credential | Input | Success body |
| --- | --- | --- | --- |
| `POST /patients/{id}/pairing` | owning doctor | None | 201 `{access_token,token_type:"bearer",patient_id,expires_at}` |
| `DELETE /patients/{id}/pairing` | owning doctor | None | 204, no body |
| `GET /me` | patient | None | 200 `{id,name,credential_expires_at}` |
| `PUT /me/devices/{device_id}` | patient | `{platform:"android"\|"ios"\|"web",fcm_token:string\|null}` | 200 `{device_id,platform,push_enabled,updated_at}` |
| `GET /me/photo-requests` | patient | None | 200 `PhotoRequest[]`, newest first |
| `GET /me/photo-requests/{id}` | owning patient | None | 200 `PhotoRequest` |
| `POST /me/photo-requests/{id}/photos` | owning patient | Multipart `file`, required `Idempotency-Key` | 201 `Photo` |
| `POST /patients/{id}/photo-requests` | owning doctor | `{instructions:string}`, required `Idempotency-Key` | 201 `PhotoRequest` |
| `POST /patients/{id}/photo-requests/{request_id}/retry-push` | owning doctor | None | 200 `PhotoRequest`; 409 if completed |
| `GET /patients/{id}/photos` | owning doctor | None | 200 `Photo[]`, newest first |
| `GET /photos/{id}/content` | owning patient/current doctor | Bearer header | 200 JPEG bytes, `Cache-Control: private, no-store` |

`PhotoRequest = {id:string,patient_id:int,instructions:string,status:"pending"|"completed",push_status:string,push_attempts:int,created_at:string,completed_at:string|null,photo:Photo|null}`. `Photo = {id:string,request_id:string,patient_id:int,mime_type:"image/jpeg",size_bytes:int,created_at:string,content_url:"/photos/<uuid>/content"}`. Exactly one accepted photo completes a request; ask for another request for a replacement.

Flutter: keep a stable installation UUID (1-128 characters); upsert the FCM token after pairing and on `onTokenRefresh`. Tokens are 1-2048 printable non-whitespace ASCII characters. PUT a null/omitted token to remove registration before logout. A token may move between the same patient's device IDs, but cannot be claimed across patients until the previous registration is cleared/revoked. Clear local credentials/cached health data on logout.

Fetch requests on startup/resume, refresh, and foreground polling (e.g. 30-60 seconds). FCM is only a hint: data is exactly `{"type":"photo_request","requestId":"<uuid>"}`, notification title `New request`, body `Open the app to view your request.` No health details. Fetch the indicated request on receive/tap and deduplicate by request ID. Polling recovers missed pushes.

Upload actual JPEG/PNG with matching file MIME `image/jpeg` or `image/png`; convert HEIC first. Let the HTTP library generate the multipart boundary. Server validates decoded bytes/size/pixels, rejects animation, normalizes to JPEG, strips EXIF/GPS/trailers, and ignores supplied filenames. Save a random operation key (1-128 characters) with the exact original upload bytes, and reuse both after timeout. Identical upload replay returns the original photo; changed key/bytes after completion returns 409. Doctor creation likewise reuses key + identical patient/instructions on retry, with no duplicate request/push. Fetch protected `content_url` bytes with the bearer header and display with `Image.memory` on web; never put tokens in image URLs.

Errors are FastAPI `{"detail":...}`: 401 missing/expired/revoked credential (re-pair), 403 wrong role, 404 absent/not owned, 409 idempotency/token/completed conflict, 413 too large, 415 malformed/unsupported/mismatched image, 422 invalid/missing fields/header. Retry transient 500/network failures with original operation keys and upload bytes. Check HTTP status before decoding success bodies.

## Delivery, checks, limitations

Requests commit before Firebase. `push_status` is `not_sent`, `disabled`, `no_devices`, `delivered` (FCM accepted, not proof of receipt), `partial`, or `failed`. Delivery failures preserve the request; known unregistered/sender-mismatch tokens are cleared without erasing concurrently refreshed tokens. Transient failures retain tokens. No automatic retry scheduler: poll or use the doctor's explicit retry-push endpoint, which may duplicate hints on already successful devices. Creation replay does not resend. FCM HTTP timeout is 10 seconds per call. Process crash before/during delivery leaves durable requests available by polling.

Run `python -m pip install -r requirements-dev.txt`, `python -m pytest -q`, and `python -m ruff check app/photos tests`. Four offline checks cover happy path/idempotency/revocation, auth/ownership, upload validation, and push-failure persistence. Tests use temporary SQLite; PostgreSQL lock concurrency and real Firebase/device delivery still need deployment smoke testing. Docker's daemon is unavailable locally. No dashboard files are changed. Filesystem/DB writes are not a distributed transaction: normal failures remove newly written files, but a crash can leave an orphan private file for offline maintenance. Production identity, auditing, retention/deletion, and versioned future migrations are not included.
