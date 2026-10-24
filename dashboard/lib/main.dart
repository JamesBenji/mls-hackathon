// Guardian doctor dashboard — Flutter (web + mobile).
// Talks to the FastAPI core at API_BASE. REST + polling (per SCOPE.md).
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

// For web/desktop dev the backend is on localhost. On a physical device,
// change this to your machine's LAN IP.
const String apiBase = String.fromEnvironment(
  'API_BASE',
  defaultValue: 'http://localhost:8000',
);

void main() => runApp(const GuardianApp());

class GuardianApp extends StatelessWidget {
  const GuardianApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Guardian',
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: const Color(0xFF1B7F79)),
        useMaterial3: true,
      ),
      home: const PatientListPage(),
    );
  }
}

Color riskColor(String? risk) {
  switch (risk) {
    case 'high':
      return const Color(0xFFD64545);
    case 'medium':
      return const Color(0xFFE09F3E);
    default:
      return const Color(0xFF3A9D5D);
  }
}

class Api {
  static Future<List<dynamic>> patients() async {
    final r = await http.get(Uri.parse('$apiBase/patients'));
    return jsonDecode(r.body) as List<dynamic>;
  }

  static Future<Map<String, dynamic>> patient(int id) async {
    final r = await http.get(Uri.parse('$apiBase/patients/$id'));
    return jsonDecode(r.body) as Map<String, dynamic>;
  }

  static Future<Map<String, dynamic>> startCall(int patientId) async {
    final r = await http.post(
      Uri.parse('$apiBase/calls/start'),
      headers: {'Content-Type': 'application/json'},
      body: jsonEncode({'patient_id': patientId}),
    );
    return jsonDecode(r.body) as Map<String, dynamic>;
  }
}

class PatientListPage extends StatefulWidget {
  const PatientListPage({super.key});
  @override
  State<PatientListPage> createState() => _PatientListPageState();
}

class _PatientListPageState extends State<PatientListPage> {
  late Future<List<dynamic>> _future;

  @override
  void initState() {
    super.initState();
    _future = Api.patients();
  }

  void _refresh() => setState(() => _future = Api.patients());

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Guardian — Patients'),
        actions: [
          IconButton(onPressed: _refresh, icon: const Icon(Icons.refresh)),
        ],
      ),
      body: FutureBuilder<List<dynamic>>(
        future: _future,
        builder: (context, snap) {
          if (snap.connectionState != ConnectionState.done) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snap.hasError) {
            return Center(child: Text('Error: ${snap.error}\n\nIs the API running at $apiBase?'));
          }
          final patients = snap.data ?? [];
          // Sort high-risk first.
          patients.sort((a, b) => _rank(b['risk_level']).compareTo(_rank(a['risk_level'])));
          return ListView.separated(
            itemCount: patients.length,
            separatorBuilder: (_, __) => const Divider(height: 1),
            itemBuilder: (context, i) {
              final p = patients[i];
              return ListTile(
                leading: CircleAvatar(
                  backgroundColor: riskColor(p['risk_level']),
                  child: Text('${p['name']}'[0]),
                ),
                title: Text('${p['name']}'),
                subtitle: Text(
                  '${p['category'] ?? '—'}  ·  '
                  '${(p['chronic_conditions'] as List).join(', ')}',
                ),
                trailing: Chip(
                  label: Text('${p['risk_level']}'.toUpperCase()),
                  backgroundColor: riskColor(p['risk_level']).withOpacity(0.15),
                ),
                onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) => PatientDetailPage(id: p['id'] as int),
                  ),
                ),
              );
            },
          );
        },
      ),
    );
  }

  int _rank(String? r) => r == 'high' ? 2 : (r == 'medium' ? 1 : 0);
}

class PatientDetailPage extends StatefulWidget {
  final int id;
  const PatientDetailPage({super.key, required this.id});
  @override
  State<PatientDetailPage> createState() => _PatientDetailPageState();
}

class _PatientDetailPageState extends State<PatientDetailPage> {
  late Future<Map<String, dynamic>> _future;

  @override
  void initState() {
    super.initState();
    _future = Api.patient(widget.id);
  }

  Future<void> _startCall() async {
    final res = await Api.startCall(widget.id);
    if (!mounted) return;
    final call = res['call'] as Map<String, dynamic>;
    showDialog(
      context: context,
      builder: (_) => AlertDialog(
        title: Text('Call: ${call['status']}'),
        content: SingleChildScrollView(
          child: Text(
            'Question plan:\n${(res['question_plan'] as List).map((q) => '• $q').join('\n')}'
            '\n\n${call['detail'] ?? ''}',
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Close'),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Patient')),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _startCall,
        icon: const Icon(Icons.phone),
        label: const Text('Start check-in call'),
      ),
      body: FutureBuilder<Map<String, dynamic>>(
        future: _future,
        builder: (context, snap) {
          if (snap.connectionState != ConnectionState.done) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snap.hasError) {
            return Center(child: Text('Error: ${snap.error}'));
          }
          final p = snap.data!;
          final vitals = (p['vitals'] as List?) ?? [];
          final meds = (p['medications'] as List?) ?? [];
          final reports = (p['reports'] as List?) ?? [];
          return ListView(
            padding: const EdgeInsets.all(16),
            children: [
              Text('${p['name']}', style: Theme.of(context).textTheme.headlineSmall),
              const SizedBox(height: 4),
              Wrap(spacing: 8, children: [
                Chip(label: Text('${p['risk_level']}'.toUpperCase()),
                    backgroundColor: riskColor(p['risk_level']).withOpacity(0.15)),
                Chip(label: Text('${p['category'] ?? '—'}')),
                Chip(label: Text('Blood: ${p['blood_type'] ?? '—'}')),
              ]),
              const SizedBox(height: 16),
              _section('Clinical', [
                'Height: ${p['height_cm'] ?? '—'} cm',
                'Weight: ${p['weight_kg'] ?? '—'} kg',
                'Chronic: ${(p['chronic_conditions'] as List).join(', ')}',
              ]),
              _listSection('Medications', meds.map((m) =>
                  '${m['name']} ${m['dose'] ?? ''}'
                  '${m['is_new_prescription'] == true ? '  (NEW)' : ''}').toList()),
              _listSection('Recent vitals', vitals.map((v) =>
                  '${v['type']}: ${v['value']} ${v['unit'] ?? ''}').toList()),
              _listSection('Past call reports (agent memory)',
                  reports.map((r) => '${r['summary']}').toList()),
            ],
          );
        },
      ),
    );
  }

  Widget _section(String title, List<String> lines) => Card(
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(title, style: const TextStyle(fontWeight: FontWeight.bold)),
              const SizedBox(height: 8),
              ...lines.map((l) => Padding(
                  padding: const EdgeInsets.symmetric(vertical: 2),
                  child: Text(l))),
            ],
          ),
        ),
      );

  Widget _listSection(String title, List<String> items) => Card(
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(title, style: const TextStyle(fontWeight: FontWeight.bold)),
              const SizedBox(height: 8),
              if (items.isEmpty) const Text('—'),
              ...items.map((l) => Padding(
                  padding: const EdgeInsets.symmetric(vertical: 2),
                  child: Text('• $l'))),
            ],
          ),
        ),
      );
}
