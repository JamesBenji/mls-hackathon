import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart';

import 'app.dart';
import 'notifications/push_notification_service.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  final android = !kIsWeb && defaultTargetPlatform == TargetPlatform.android;
  runApp(
    MedicallApp(pushNotifications: android ? PushNotificationService() : null),
  );
}
