import 'package:flutter/material.dart';
import 'package:forui/forui.dart';

import 'features/home_screen.dart';
import 'notifications/push_notification_service.dart';
import 'ui/care_widgets.dart';

class MedicallApp extends StatelessWidget {
  const MedicallApp({this.pushNotifications, super.key});

  final PushNotificationService? pushNotifications;

  @override
  Widget build(BuildContext context) {
    final theme = FThemeData(
      touch: true,
      colors: FColors.neutralLight.copyWith(
        primary: CareColors.teal,
        foreground: CareColors.ink,
        background: CareColors.background,
        secondary: CareColors.mint,
        border: CareColors.border,
      ),
    );

    return MaterialApp(
      title: 'Medicall · Your care, connected',
      debugShowCheckedModeBanner: false,
      theme: theme.toApproximateMaterialTheme(),
      localizationsDelegates: FLocalizations.localizationsDelegates,
      supportedLocales: FLocalizations.supportedLocales,
      builder: (context, child) => FTheme(
        data: theme,
        child: FToaster(child: child!),
      ),
      home: HomeScreen(pushNotifications: pushNotifications),
    );
  }
}
