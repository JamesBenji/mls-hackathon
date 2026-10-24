import 'package:flutter/material.dart';
import 'package:forui/forui.dart';
import 'package:http/http.dart' as http;
import 'screens/dashboard_screen.dart';
import 'services/dashboard_api.dart';

const teal = Color(0xFF16766D);
const mint = Color(0xFFE5F4EE);
const ink = Color(0xFF183D38);

class GuardianApp extends StatefulWidget {
  final DashboardApi? api;
  const GuardianApp({super.key, this.api});
  @override
  State<GuardianApp> createState() => _GuardianAppState();
}

class _GuardianAppState extends State<GuardianApp> {
  late final http.Client? _client = widget.api == null ? http.Client() : null;
  late final DashboardApi _api =
      widget.api ?? HttpDashboardApi(client: _client!);
  @override
  void dispose() {
    _client?.close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final theme = FThemeData(
      colors: FColors.neutralLight.copyWith(
        primary: teal,
        foreground: ink,
        secondary: mint,
        secondaryForeground: ink,
        border: const Color(0xFFDCE8E3),
      ),
      touch: true,
    );
    return MaterialApp(
      title: 'Guardian · Clinical workspace',
      debugShowCheckedModeBanner: false,
      localizationsDelegates: FLocalizations.localizationsDelegates,
      supportedLocales: FLocalizations.supportedLocales,
      theme: theme.toApproximateMaterialTheme().copyWith(
        scaffoldBackgroundColor: const Color(0xFFF5F8F6),
      ),
      builder: (context, child) => FTheme(
        data: theme,
        child: FTooltipGroup(child: child!),
      ),
      home: DashboardScreen(api: _api),
    );
  }
}
