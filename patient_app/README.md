# Medicall patient UI

A Flutter + Forui prototype of the patient photo-request flow. Android is configured for real Firebase Cloud Messaging on project `medicall-demo-96823`. Photos and submissions remain local-only demos. Other platforms retain the standalone UI without Firebase initialization.

## Run

This app now lives in the main repository under `patient_app/`. Run the commands below from that directory. The doctor desktop app remains a separate Flutter package in `dashboard/`; the server lives in `backend/`.

```sh
flutter pub get
flutter run -d chrome
```

For a connected Android or iOS device, run `flutter run` and select the device. iOS builds require macOS and signing configuration.

## Demo

1. Open the seeded photo request on Home, or press **Test notification** to display a simulated in-app notification.
2. Open the request and select a photo. Android/iOS also offer camera capture; web uses the photo/file picker.
3. Review or remove the photo, then press **Send photo**.
4. A local success screen appears and Home/Updates show completion. Press **Test notification** to reset the request and repeat.

Photos are held in memory for preview and never uploaded. Submission is simulated and state resets on app restart. The **Test notification** button remains an in-app simulation; real Android push is a separate flow below. All names and care information are fictional. Do not use real patient data in this prototype.

## Real Android push test

1. Connect your Android phone with USB debugging enabled and run `flutter run` (use `flutter devices` to select a device).
2. On Home, scroll down to **Notifications**, press **Enable notifications**, and allow Android notification permission.
3. Press **Copy FCM token**. Paste the token into Firebase Console → Messaging → notification campaign → **Send test message**. The token is also selectable in the app. It is not uploaded to a backend.
4. Use a generic title/body such as `New photo request` / `Open the app to respond.` Press the phone's Home button to background the app, then send the test message.
5. Tap the Android notification. It opens the seeded photo request. To test cold start, swipe the app out of Recents before sending; do not force-stop it in Settings.
6. A message received with the app in the foreground produces an in-app banner and snackbar, not an Android system notification.

For backend messages, include string data fields `type: photo_request` and `requestId: demo-photo-request`, alongside a `notification` title/body. Unknown request IDs are explicitly rejected until a request-loading backend exists. Console messages without custom data intentionally open the demo fixture. Push delivery is best-effort, not suitable for emergency alerts. No local notification plugin is used. Firebase client configuration is not a service-account credential; never put Admin SDK credentials in the app.

If Android builds report missing SDKs or licenses, install Android SDK Platform 36 and the required build tools through Android Studio's SDK Manager and run `flutter doctor --android-licenses` to review the license prompts.

## Backend handoff

`lib/data/photo_requests.dart` contains the request model, fixture, and local state. Replace the demo store with a repository backed by the eventual API. `PhotoRequestScreen` takes a request explicitly so a future notification router can load a request by ID and open the same UI. Replace simulated submission with an upload before recording completion. The picker is currently local to the request screen and does not persist drafts.

## Checks

```sh
flutter analyze
flutter test
flutter build web
```
