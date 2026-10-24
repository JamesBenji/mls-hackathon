import 'dart:async';

import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';

import '../data/photo_requests.dart';
import '../firebase_options.dart';

/// Console test messages without routing data open the seeded demo request.
String? requestIdFromMessage(RemoteMessage message) {
  final type = message.data['type'];
  if (type != null && type != 'photo_request') {
    return null;
  }
  final id = message.data['requestId'];
  if (id is String && id.isNotEmpty) {
    return id;
  }
  return message.notification != null ? demoPhotoRequest.id : null;
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

  Future<void> initialize() async {
    busy = true;
    _notify();
    try {
      await Firebase.initializeApp(
        options: DefaultFirebaseOptions.currentPlatform,
      );
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
        _messaging!.onTokenRefresh.listen((value) {
          token = value;
          _notify();
        }),
      );
      permission =
          (await _messaging!.getNotificationSettings()).authorizationStatus;
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
