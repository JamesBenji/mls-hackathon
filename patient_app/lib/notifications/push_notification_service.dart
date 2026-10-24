import 'dart:async';

import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';

import '../firebase_options.dart';

/// Messages without a real backend request ID never invent a request.
String? requestIdFromMessage(RemoteMessage message) {
  final type = message.data['type'];
  if (type != null && type != 'photo_request') {
    return null;
  }
  final id = message.data['requestId'];
  if (id is String && id.isNotEmpty) {
    return id;
  }
  return null;
}

class PushNotificationService extends ChangeNotifier {
  final _opened = StreamController<String>.broadcast();
  final _foreground = StreamController<String>.broadcast();
  final List<StreamSubscription<dynamic>> _subscriptions = [];
  FirebaseMessaging? _messaging;
  bool _disposed = false;

  Stream<String> get openedRequests => _opened.stream;
  Stream<String> get foregroundRequests => _foreground.stream;

  bool busy = false;
  String? token;
  String? error;
  AuthorizationStatus? permission;
  Future<void> Function(String? token)? registerToken;
  bool registered = false;
  bool _initialized = false;

  Future<void> initialize() async {
    if (_initialized || busy) return;
    busy = true;
    _notify();
    try {
      if (Firebase.apps.isEmpty) {
        await Firebase.initializeApp(
          options: DefaultFirebaseOptions.currentPlatform,
        );
      }
      if (_disposed) {
        return;
      }
      _messaging = FirebaseMessaging.instance;
      _subscriptions.add(
        FirebaseMessaging.onMessage.listen((message) {
          final id = requestIdFromMessage(message);
          if (id != null) {
            _foreground.add(id);
          }
        }),
      );
      _subscriptions.add(
        FirebaseMessaging.onMessageOpenedApp.listen((message) {
          final id = requestIdFromMessage(message);
          if (id != null) {
            _opened.add(id);
          }
        }),
      );
      // The Firebase Console's test dialog requires the legacy registration
      // token, not the FID emitted by the newer onRegistered API.
      _subscriptions.add(
        // ignore: deprecated_member_use
        _messaging!.onTokenRefresh.listen(
          (value) async {
            token = value;
            await _register();
            _notify();
          },
          onError: (_) {
            error =
                'Notification token refresh failed. Reconnect notifications.';
            _notify();
          },
        ),
      );
      permission =
          (await _messaging!.getNotificationSettings()).authorizationStatus;
      _initialized = true;
      await syncToken();
      final initial = await _messaging!.getInitialMessage();
      if (!_disposed && initial != null) {
        final id = requestIdFromMessage(initial);
        if (id != null) {
          _opened.add(id);
        }
      }
    } catch (_) {
      error = 'Can’t connect. Check your internet and retry.';
    } finally {
      busy = false;
      _notify();
    }
  }

  Future<void> _register() async {
    registered = false;
    try {
      if (registerToken == null || token == null) return;
      await registerToken!(token);
      registered = true;
      error = null;
    } catch (_) {
      error =
          'Could not register this phone with your care server. Reconnect notifications; manual request refresh still works.';
    }
  }

  Future<void> syncToken() async {
    if (_messaging == null || _disposed) return;
    try {
      permission =
          (await _messaging!.getNotificationSettings()).authorizationStatus;
      if (permission == AuthorizationStatus.authorized ||
          permission == AuthorizationStatus.provisional) {
        // ignore: deprecated_member_use
        token = await _messaging!.getToken();
        await _register();
      } else {
        registered = false;
        await registerToken?.call(null);
      }
    } catch (_) {
      error =
          'Could not refresh notification registration. Retry from your connection settings.';
    }
    _notify();
  }

  Future<void> enableNotifications() async {
    if (busy) {
      return;
    }
    if (_messaging == null) {
      await initialize();
    }
    if (_messaging == null || _disposed) {
      return;
    }
    busy = true;
    error = null;
    _notify();
    try {
      permission = (await _messaging!.requestPermission()).authorizationStatus;
      if (permission == AuthorizationStatus.denied) {
        error =
            'Allow notifications in Settings → Apps → Medicall → Notifications.';
        return;
      }
      // Firebase Console still expects an FCM registration token for testing.
      // ignore: deprecated_member_use
      token = await _messaging!.getToken();
      if (token == null) {
        error = 'Token not ready. Try again.';
      } else {
        await _register();
      }
    } catch (_) {
      error = 'Setup failed. Check your internet and retry.';
    } finally {
      busy = false;
      _notify();
    }
  }

  void _notify() {
    if (!_disposed) {
      notifyListeners();
    }
  }

  @override
  void dispose() {
    _disposed = true;
    for (final subscription in _subscriptions) {
      unawaited(subscription.cancel());
    }
    unawaited(_opened.close());
    unawaited(_foreground.close());
    super.dispose();
  }
}
