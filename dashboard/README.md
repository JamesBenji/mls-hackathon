# Medicall · Follow-up plans

Independent Flutter package with a Windows-first Forui workspace and a stacked narrow-screen fallback. Medicall sets follow-up calls and what to track; it is not an EHR. The default `lib/main.dart` launches `MedicallApp`.

## Safe local preview

Requires Flutter 3.44+/Dart 3.12 and the Visual Studio desktop C++ workload for Windows.
Run from `dashboard/`:

```sh
flutter pub get
flutter run -d windows --dart-define=SAMPLE_DATA=true
```

Sample mode constructs only `SamplePlanApi`, which has no HTTP client or network code. Edits, drafts, activation, pause and finish work in memory until restart. **Call now never places a call or adds fabricated call results.** Select Alex Morgan under Needs review for wound pain/redness/photo observations; select Sam Taylor under Active for cough/sleep/free-text breathing observations. Fixtures include multiple calls, numeric trends and missing/unclear answers. They are synthetic sample history, not live results. Regeneration uses a fixed respiratory/wound mapping, not an actual Gemma invocation. No backend, API key, inference service or phone provider is needed. The existing unrelated preview app is not used.

Preview/test disclaimers are intentionally absent from the UI; the launch command is what selects this behavior. Removing those labels does not enable networking or real calling. Call now in sample mode is a no-op and does not claim successful dispatch. All preview history remains synthetic, including its unlabeled patient quotes and completed-call fixtures. The workspace initially opens Active with the first matching plan selected.

## Desktop layout and active control audit

- At 900+ logical pixels: a persistent 176px navigation rail, horizontal title/count/search/action toolbar, 272px master list, and independently scrolling detail pane. At 1280px, Refresh/New plan and detail actions remain compact horizontal buttons, not full-width rows. `WorkspaceButton` uses both `IntrinsicWidth` and Forui `mainAxisSize: min`; only sidebar choices intentionally fill their bounded rail slot.
- Results use two columns when their own available width permits, otherwise one. The readable AI interpretation is expandable so the instructions and patient-reported trend cards remain the primary hierarchy. Below the desktop breakpoint, status filters scroll horizontally and the list/detail stack in one scrollable page.
- Verified the rebuilt Windows window at 1280px using a native window capture: persistent rail, intrinsic-width toolbar/action buttons, split panes, two-column result cards and the new launcher icon. The master list's remaining space reflects the small fixture dataset, not stretched controls.
- Active graph: `main.dart` → `medicall_app.dart` → `follow_up_workspace.dart`, `plan_detail.dart`, `plan_editor.dart`, `plan_results.dart`, `workspace_widgets.dart`. It does **not** import `app.dart` or `clinical_widgets.dart`.
- Direct active interactive controls: Forui `FTextField`/`FTextFormField`, `FSelect`, `FButton`, `FTappable`, `FDialog`/`showFDialog`, `FAccordion`, and `FProgress`/`FCircularProgress`. There are no direct Material text fields, dropdowns, checkboxes, dialogs, expansion tiles, snackbars or progress indicators on these routes. MaterialApp/Scaffold/route hosting and Flutter layout/rendering primitives remain; the chart is a CustomPainter with a keyboard-accessible Forui disclosure/table alternative.
- Isolated legacy libraries retained without broad refactoring: `app.dart`, `screens/dashboard_screen.dart`, `patient_detail_screen.dart`, `clinical_tools.dart`, `record_editor.dart`, `triage_panel.dart`, `widgets/clinical_widgets.dart`, and their old models/API/tests. They are not routes or a second executable entrypoint in the default app. Their old Material controls are not part of the active widget tree.
- Windows/Web launcher assets are the parent-generated Medicall branding files, included unchanged. Windows FileDescription/ProductName are Medicall. No patient-app or backend assets are changed here.

## Backend session

```sh
flutter run -d windows
```

Enter server address, X-API-Key and doctor ID in the connection screen. Values are held only for this session and not saved. Disconnect closes the client and clears the key. `API_BASE`, `DOCTOR_ID` and optionally `API_KEY` are compile-time **development-only** defaults; never bake a shared production secret into a distributable. Use HTTPS beyond localhost. A shared key is not a doctor login and provides no per-doctor authorization; doctor ID explicitly selects the owner of newly drafted plans, not an authenticated identity. The list is whatever the server returns, not an access-control boundary.

Backend follow-up-plan endpoints from [PR #4](https://github.com/JamesBenji/mls-hackathon/pull/4) must be deployed first. The UI is aligned to `backend/FOLLOW_UP_CONTRACT.md` from that PR. Until deployment use sample mode; no production availability is implied. Web fallback: `flutter run -d chrome --dart-define=SAMPLE_DATA=true`. Real web sessions also need backend CORS and compatible HTTPS.

## Workflow

- Active / Needs review (draft and paused) / Finished lists, search, refresh, loading/error/retry and empty states.
- New plan selects an existing patient via `GET /patients` and submits doctor instructions. A 422 clarification is shown so instructions can be corrected; it never activates automatically.
- The doctor writes **natural-language instructions only**, with schedule controls. Gemma derives context, typed tracking items, questions and photo policy. These appear as a readable, read-only interpretation, not a field/template/schema editor. Correct the instructions to regenerate the interpretation. Saving never activates.
- Activation asks for explicit confirmation; pause an active plan before editing. Reactivation is explicit. Finish stops follow-up. Call now is available only for active plans with a returned phone number and requires confirmation. Pending actions lock duplicate requests.
- Results derive from per-plan item definitions and observations: latest-answer cards, numeric trends with missing-data gaps, categorical/boolean timelines, text quotes, and expandable per-call history. Each series is isolated by item ID, plan revision and unit; unknown revisions do not get a numeric chart. Latest missing/unclear/declined answers are never replaced with older values. Charts show observed values, not AI-inferred target ranges, and have expandable textual tables. Reported and measured timestamps are labeled separately; select a chart position or expand a row to inspect the patient quote and call context. Patient-reported observations are separate from AI interpretation/summaries. No polling or fabricated live status. Photo content is not fetched without a specified authenticated content contract; the attachment state remains visible.

## Contract

`HttpPlanApi` sends `X-API-Key` on all requests and preserves an API base path prefix.

| Operation | Endpoint / body |
|---|---|
| List / detail | `GET /follow-up-plans`, `GET /follow-up-plans/{id}` |
| Existing identities | `GET /patients` array with `id`, `name`, `phone` (optional timezone) |
| Generate draft | `POST /follow-up-plans/draft` with `{doctor_id, patient_id, instructions}` → plan or 422 `detail` |
| Edit | `PATCH /follow-up-plans/{id}` with `instructions`, `schedule`; changed instructions must regenerate derived tracking definitions and advance the plan revision |
| Actions | `POST /follow-up-plans/{id}/activate`, `/pause`, `/finish`, `/call` |

Photo policy: `none|first_call|every_call`. Schedule: `timezone` (IANA), `local_time` (HH:mm), `frequency` (`once|daily`), `start_date`, `end_date` (YYYY-MM-DD). The server must validate timezone, scheduling semantics, authorization, transitions and real call safety. Mutations may return an empty success response; the UI fetches detail again. No patient creation API is assumed.

Plan fields: `id`, `doctor_id`, `patient_id`, `patient_name`, `instructions`, `context`, `questions`, `tracked_items`, `photo_policy`, nullable `schedule`, `status` (`draft|active|paused|finished`), nullable `next_call_at`, nullable `last_summary`, `revision`, optional `calls`. A draft without a schedule must be edited before activation. Action responses including `/call` are updated plan dictionaries; the UI fetches detail again after success. Only changed instructions/schedule fields are patched.

Typed `tracked_items`: `{id,label,type:number|boolean|choice|text|photo,unit?,min?,max?,options?:string[]}`. Legacy strings remain readable. `min/max` are instructed scale bounds, never inferred clinical targets. `observations`: `{id,item_id,plan_revision,call_session_id,value,status:reported|missing|unclear|declined,unit,source_quote,reported_at,measured_at?,definition_snapshot}`. Plan `revision` identifies current observations. Immutable observation snapshots supply historical labels/types/units/scales, including items removed from the current plan. Distinct IDs/revisions/units or incompatible snapshot bounds/types/options never share a chart line. Missing or incompatible observations break the line; missing is never zero. Charts use report-order positions (explicitly labeled), not elapsed-time spacing. Backend naive ISO timestamps are explicitly interpreted/displayed as UTC, separate from the schedule's IANA timezone.

**Integration boundaries:** PR #4 guarantees instruction regeneration, meaningful-edit revision increments, immutable observation snapshots and explicit activation. Calls are `{id,call_session_id,status,started_at,summary,results}`; summary/results are labeled AI interpretation, never patient measurements. Immediate `/call` is an extra call and leaves the next scheduled slot intact. Pause/finish block future work but do not hang up an in-progress call. Photo observation values can identify verified uploaded assets, but dashboard-authenticated image-content retrieval is not specified, so no image requests or guessed URLs are made. Shared-key doctor authorization remains a limitation; use a trusted proxy until identity-bound auth exists. Timeout errors instruct refresh before retry because the server may already have accepted a write; no automatic write retries. No deployed backend/Gemma/telephony/FCM integration has been verified.

## Structure and checks

`medicall_app.dart` owns theme/session configuration; `models/follow_up_plan.dart` owns plan/identity models; `services/plan_api.dart` defines the contract and HTTP client; `sample_plan_api.dart` is isolated in-memory preview data; workspace, editor and detail are separate screens. Existing `GuardianApp`, clinical screens and their tests remain available as legacy code, but are not reachable from the main workspace. The legacy analysis adapter accepts nested `{analysis:{report,triage}}` and older flat analysis.

```sh
dart format --output=none --set-exit-if-changed lib test
flutter analyze
flutter test
flutter build windows --dart-define=SAMPLE_DATA=true
```

Official [Forui docs](https://forui.dev/docs/llms.txt) and pinned 0.25 package source were used. Tests use local fakes/mocks, never the real backend or a calling provider. No backend files are changed.
