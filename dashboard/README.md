# Guardian clinical workspace

A responsive Flutter doctor dashboard using Forui with a teal/mint clinical theme. Includes the web scaffold only; the layout also adapts to desktop and narrow mobile widths.

## Run

Requires Flutter 3.44 or newer and Dart 3.12. The lockfile pins Forui 0.25.0, the current compatible release resolved with Flutter 3.44.8. Forui APIs were checked against the [official documentation](https://forui.dev/docs/llms.txt) and installed package source.

```sh
flutter pub get
flutter run -d chrome --dart-define=API_BASE=http://localhost:8000
flutter build web --dart-define=API_BASE=https://your-api.example.org
```

Run commands from `dashboard/`. `API_BASE` is compile-time configuration, defaults to `http://localhost:8000`, and supports a path prefix. Rebuild when changing it. Use a reachable LAN address rather than localhost for a physical device. Serve `build/web/` over HTTP(S); HTTPS dashboards require an HTTPS API and the backend must permit the dashboard's origin through CORS. No credentials belong in a Dart define: compiled web configuration is public.

## Screens and behavior

- **Patient overview:** risk-prioritized directory, name/phone/condition/ID search, risk filters, counts derived from returned records, explicit manual refresh with a last-refresh time, and loading/empty/error/retry states. Desktop uses a sidebar; narrow layouts use compact navigation and stacked cards.
- **Patient record:** clinical overview, medication doses/schedules, recorded vitals sorted newest first, report summaries, and profile editing. Profile edits support height, weight, risk and comma-separated chronic conditions; empty height/weight clears those fields. Manual vitals use explicit measurement types and units, numeric validation and a server-assigned timestamp. Saves refresh details, and returning to the directory refreshes the patient list.
- **Triage review:** confirm the recommendation or choose an override, then explicitly record the doctor's review via the existing API. The backend marks a review complete; it does not execute a callback, ambulance request or appointment. The UI states this before and after submission.
- **Clinical support:** question-plan generation and transcript analysis run only after a button press, never on page load or refresh. These operations may invoke MedGemma and have a 120-second timeout. Analysis is displayed as an unsaved result; no fabricated call-session ID is sent, so no report or triage decision is persisted. Refreshing details clears generated guidance to avoid retaining results based on an old profile.

No calls, scheduling, autonomous follow-up, fabricated live status or sample patient data are presented as operational features. Follow-up remains doctor-directed through the care team's existing workflow because this backend does not expose those write APIs. Database timestamps are shown as returned; the current backend's naive timestamps do not establish a timezone.

## Architecture and integration

`lib/services/dashboard_api.dart` defines the injectable `DashboardApi` interface and its HTTP implementation. `lib/models/` contains parsing, `lib/screens/` owns screen state and forms, and `lib/widgets/` contains shared Forui presentation. `GuardianApp(api: ...)` accepts a fake or authenticated implementation. The HTTP client is injected and owned by the app, so auth headers can be added by a wrapping client without rewriting screens. The app closes only the client it creates.

Existing endpoints used: `GET /patients`, `GET/PATCH /patients/{id}`, `POST /patients/{id}/vitals`, `GET /triage`, `POST /triage/{id}/review`, `GET /clinical/{id}/question-plan`, and `POST /clinical/{id}/analyze`. Vitals are read from the existing patient detail payload, avoiding redundant requests to `GET /patients/{id}/vitals`.

All requests check success status, handle network/JSON/schema failures and have bounded timeouts (25 seconds normally). Pending actions disable duplicate submissions. A timeout cannot cancel server-side processing; refresh before retrying a write to avoid duplicate vital entries. HTTP errors intentionally do not display raw backend bodies or sensitive tracebacks. No automatic polling, retries, local persistence or patient-data logging is used.

For photo-sync integration, add a separate photo API/service and photo section to `patient_detail_screen.dart` rather than embedding networking in UI. This change does not touch `backend/`. A parallel PR that changes the original `lib/main.dart` will need to move those additions into the new app/service/detail-screen split; retain `main.dart` as the tiny entry point. If auth becomes mandatory, inject an authenticated HTTP client/API and implement the required sign-in flow; this dashboard does not pretend to provide authentication.

## Security limitations

The existing backend APIs are unauthenticated, expose patient records by ID, accept writes without authorization, and allow broad CORS. The dashboard does not fix those server limitations and must not be publicly deployed with real patient data. Production use needs server-enforced authentication, doctor/patient authorization, restricted CORS, transport security, audit logging, appropriate data governance and server-side validation. Client form validation is only a usability aid, not a security boundary. There is no invented doctor identity or misleading login screen.

## Checks

```sh
dart format --output=none --set-exit-if-changed lib test
flutter analyze
flutter test
flutter build web --dart-define=API_BASE=http://localhost:8000
```

Tests use injected fakes and `http`'s mock client; they do not require backend services or inference. Coverage includes parsing/risk order, HTTP/network/schema/timeout failures, endpoint payloads, loading/empty/retry states, duplicate refresh prevention, directory search/filtering, narrow/tablet/desktop layouts, real triage submission wiring, explicit inference and profile validation. A successful web build does not replace testing against a running backend; no live server or MedGemma verification is implied.
