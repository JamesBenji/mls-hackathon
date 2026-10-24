# Medicall patient app

Flutter + Forui patient photo-request app. Uses patient-scoped bearer authentication; no guardian/service keys belong in this app.

## Connect

The care team obtains a patient token from `POST /patients/{patient_id}/pairing` using a **doctor-scoped bearer credential**, verifies the patient's identity, and transfers that token privately once. Enter it in the app's pairing screen. `/me` validates patient scope before the session is stored with `flutter_secure_storage`. Expired/revoked credentials require a new care-team pairing token; there is no refresh-token endpoint.

The HTTPS care server defaults to `https://medicall.nulkode.dev`. Override on the pairing screen or at build time:

```sh
flutter run --dart-define=BACKEND_BASE_URL=https://your-server.example
flutter build apk --debug --target-platform android-arm64
```

## Backend contract

- Bearer `GET /me`, `GET /me/photo-requests`, `GET /me/photo-requests/{id}`.
- Bearer `PUT /me/devices/{device_id}` with `{fcm_token, platform}`; persisted installation ID, automatic FCM registration/refresh, null token on disconnect.
- Bearer `POST /me/photo-requests/{id}/photos`, multipart `file`, `Idempotency-Key`. A selected image keeps its key for retries. New selections/retakes get new keys.
- Push data must contain `type: photo_request`, `requestId: <real request UUID>`. No-data notifications never invent requests. Cold-start/open events fetch that exact ID. Foreground events expose an open action and refresh the list.
- Request fetching occurs at startup/resume and with visible refresh controls. Push is best effort, not required for request discovery.
- Upload success is separate from advisory quality. Poll `quality.status` every four seconds, at most fifteen automatic checks; manual refresh can restart checking. Original uploads remain available during uncertain/failed checks. Optional retakes use the same request only for `retake_recommended|uncertain|failed`; the backend replaces the original only once persistence succeeds. No historical-retake gallery exists.

Android FCM uses the existing Firebase client configuration. Server-side Firebase Admin credentials and push enablement must be configured separately. iOS requires its own Firebase/signing/APNs setup before push is enabled there. Camera/gallery files are previewed in memory and not persisted as drafts. Android picker lost-data recovery is supported when reopening a request.

## Essential checks

```sh
flutter pub get
flutter analyze
flutter test
flutter build apk --debug --target-platform android-arm64
```

No live backend, model, FCM-send, provisioning, or phone calls are needed for these checks.
