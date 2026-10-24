import 'package:flutter/material.dart';
import 'package:forui/forui.dart';
import 'package:http/http.dart' as http;
import 'screens/follow_up_workspace.dart';
import 'services/plan_api.dart';
import 'services/sample_plan_api.dart';
import 'widgets/workspace_widgets.dart';

const sampleData = bool.fromEnvironment('SAMPLE_DATA');

class MedicallApp extends StatelessWidget {
  final PlanApi? api;
  const MedicallApp({super.key, this.api});
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
      touch: false,
    );
    return MaterialApp(
      title: 'Medicall · Follow-up plans',
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
      home: api != null
          ? FollowUpWorkspace(api: api!, doctorId: 1, sample: true)
          : const SessionScreen(),
    );
  }
}

class SessionScreen extends StatefulWidget {
  const SessionScreen({super.key});
  @override
  State<SessionScreen> createState() => _SessionScreenState();
}

class _SessionScreenState extends State<SessionScreen> {
  final _form = GlobalKey<FormState>();
  final _server = TextEditingController(
    text: const String.fromEnvironment(
      'API_BASE',
      defaultValue: 'http://localhost:8000',
    ),
  );
  final _key = TextEditingController(
    text: const String.fromEnvironment('API_KEY'),
  );
  final _doctor = TextEditingController(
    text: const String.fromEnvironment('DOCTOR_ID', defaultValue: '1'),
  );
  http.Client? _client;
  PlanApi? _api;
  int _doctorId = 1;
  @override
  void initState() {
    super.initState();
    if (sampleData) _api = SamplePlanApi();
  }

  @override
  void dispose() {
    _server.dispose();
    _key.dispose();
    _doctor.dispose();
    _client?.close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (_api != null) {
      return FollowUpWorkspace(
        api: _api!,
        doctorId: _doctorId,
        sample: sampleData,
        onDisconnect: sampleData
            ? null
            : () => setState(() {
                _client?.close();
                _client = null;
                _api = null;
                _key.clear();
              }),
      );
    }
    return Scaffold(
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: SizedBox(
            width: 460,
            child: SectionCard(
              title: 'Connect to Medicall',
              subtitle:
                  'Session-only connection. The shared API key is not a doctor login; doctor ID selects the plan owner.',
              child: Form(
                key: _form,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    FTextFormField(
                      control: FTextFieldControl.managed(controller: _server),
                      label: const Text('Server address'),
                      validator: (v) {
                        final uri = Uri.tryParse(v?.trim() ?? '');
                        return uri == null ||
                                !['http', 'https'].contains(uri.scheme) ||
                                uri.host.isEmpty ||
                                uri.hasQuery ||
                                uri.hasFragment
                            ? 'Enter an HTTP(S) server address.'
                            : null;
                      },
                    ),
                    const SizedBox(height: 16),
                    FTextFormField(
                      control: FTextFieldControl.managed(controller: _key),
                      obscureText: true,
                      label: const Text('API key'),
                      validator: (v) => v == null || v.trim().isEmpty
                          ? 'Enter the server API key.'
                          : null,
                    ),
                    const SizedBox(height: 16),
                    FTextFormField(
                      control: FTextFieldControl.managed(controller: _doctor),
                      label: const Text('Doctor ID'),
                      keyboardType: TextInputType.number,
                      validator: (v) => (int.tryParse(v ?? '') ?? 0) <= 0
                          ? 'Enter a positive doctor ID.'
                          : null,
                    ),
                    const SizedBox(height: 24),
                    Align(
                      alignment: Alignment.centerLeft,
                      child: WorkspaceButton(
                        onPress: () {
                          if (!_form.currentState!.validate()) return;
                          setState(() {
                            _doctorId = int.parse(_doctor.text);
                            _client = http.Client();
                            _api = HttpPlanApi(
                              client: _client!,
                              baseUrl: _server.text.trim(),
                              apiKey: _key.text.trim(),
                            );
                          });
                        },
                        child: const Text('Connect'),
                      ),
                    ),
                    const SizedBox(height: 16),
                    const Text(
                      'Use HTTPS outside localhost. Credentials are not saved.',
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
