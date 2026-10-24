# Server-only patient pairing bootstrap

Use this only as an authorized operator while doctor sign-in/pairing UI is
unavailable. It does not bypass the doctor-scoped HTTP pairing endpoint or exchange
the shared `GUARDIAN_API_KEY` for a patient token.

1. In the **live, backend-connected dashboard** (not sample/demo mode), open the
   intended existing patient's detail page. Its subtitle shows **Patient #N**;
   patient list cards also show **#N**. Verify the person's identity and their
   associated doctor before issuing anything. Never guess an ID.
2. Deploy an image containing this script. In Coolify, open this resource's
   **Terminal**, select the running **api** container, and run from `/app`:

   ```sh
   python -m scripts.issue_patient_pairing --patient-id N
   ```

   Replace `N` with that verified numeric ID. The container already has the live
   database environment. Local equivalent: run from `backend/` with the intended
   database configuration. No confirmation flag is needed: invoking the command
   deliberately issues a credential.
3. Success prints exactly one JSON object with `access_token`,
   `token_type` (`bearer`), `patient_id`, and `expires_at` (ISO datetime, UTC,
   matching `POST /patients/{id}/pairing`). Copy only `access_token` into the
   patient app's token-entry/pairing screen **when that upcoming UI is available**;
   use the deployed HTTPS API origin. Do not put server API keys in the app.

The token is a secret granting access to that patient's data. Transfer it privately
to the verified patient; do not share terminal output, screenshots, support/CI
logs, chat messages, or recordings. The command deliberately reveals it on stdout
only after commit; it is not retrievable from the database, which stores its hash.
Avoid logging/capturing the terminal session.

Missing patient/doctor records fail without issuing a credential. Database/schema
errors fail without printing a token; diagnose privately. The command never creates
patients/doctors/tables, seeds demo identities, resets data, or starts the API.
Each successful invocation adds a patient-only credential and does not revoke
previous ones. Expiry uses the existing `PHOTO_CREDENTIAL_DAYS` setting. Existing
doctor-authorized `DELETE /patients/{id}/pairing` revokes that patient's credentials;
this bootstrap does not mint doctor credentials or weaken API authorization.
