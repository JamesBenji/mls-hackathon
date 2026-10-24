import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:guardian_dashboard/app.dart';
import 'package:guardian_dashboard/models/patient.dart';
import 'package:guardian_dashboard/services/dashboard_api.dart';
import 'package:guardian_dashboard/screens/patient_detail_screen.dart';

class FakeApi implements DashboardApi {
  int planRequests = 0, reviewRequests = 0, patientRequests = 0, writes = 0;
  bool fail = false, empty = false;
  Completer<List<Patient>>? pending;
  final records = [
    Patient.fromJson({
      'id': 1,
      'name': 'Alex Morgan',
      'risk_level': 'high',
      'category': 'Cardiology',
      'chronic_conditions': ['Hypertension'],
      'medications': [
        {'name': 'Amlodipine', 'dose': '5 mg', 'schedule': 'Daily'},
      ],
      'vitals': [
        {'type': 'bp_systolic', 'value': 130, 'unit': 'mmHg'},
      ],
      'reports': [
        {'summary': 'Symptoms improving.'},
      ],
    }),
    Patient.fromJson({'id': 2, 'name': 'Sam Taylor', 'risk_level': 'low'}),
  ];
  @override
  Future<List<Patient>> patients() async {
    patientRequests++;
    if (pending != null) return pending!.future;
    if (fail) throw const ApiException('Cannot reach the server.');
    return empty ? [] : records;
  }

  @override
  Future<Patient> patient(int id) async =>
      records.firstWhere((p) => p.id == id);
  @override
  Future<List<TriageDecision>> triage() async => reviewRequests > 0
      ? []
      : [
          TriageDecision.fromJson({
            'id': 4,
            'patient_id': 1,
            'urgency': 'urgent',
            'recommended_action': 'doctor_callback',
            'rationale': 'Persistent symptoms',
          }),
        ];
  @override
  Future<void> review(int id, {String? overrideAction}) async {
    reviewRequests++;
  }

  @override
  Future<void> updatePatient(int id, Json changes) async {
    writes++;
  }

  @override
  Future<void> addVital(int id, Json vital) async {
    writes++;
  }

  @override
  Future<List<String>> questionPlan(int id) async {
    planRequests++;
    return ['How are you feeling?'];
  }

  @override
  Future<Json> analyze(int id, String transcript) async => {
    'summary': 'Stable',
  };
}

void main() {
  for (final width in [360.0, 768.0, 1440.0]) {
    testWidgets('directory search and risk filters fit ${width.toInt()}px', (
      tester,
    ) async {
      await tester.binding.setSurfaceSize(Size(width, 1000));
      addTearDown(() => tester.binding.setSurfaceSize(null));
      final api = FakeApi();
      await tester.pumpWidget(GuardianApp(api: api));
      await tester.pumpAndSettle();
      expect(find.text('Patient overview'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.scrollUntilVisible(
        find.text('Alex Morgan'),
        300,
        scrollable: find.byType(Scrollable).first,
      );
      expect(find.text('Sam Taylor'), findsOneWidget);
      await tester.enterText(find.byType(TextField).first, 'Hypertension');
      await tester.pumpAndSettle();
      expect(find.text('Alex Morgan'), findsOneWidget);
      expect(find.text('Sam Taylor'), findsNothing);
      await tester.enterText(find.byType(TextField).first, '');
      await tester.tap(find.text('Low').first);
      await tester.pumpAndSettle();
      expect(find.text('Alex Morgan'), findsNothing);
      expect(find.text('Sam Taylor'), findsOneWidget);
      expect(api.planRequests, 0);
      expect(tester.takeException(), isNull);
    });
  }
  testWidgets('loading blocks duplicate refresh and errors allow retry', (
    tester,
  ) async {
    final api = FakeApi()..pending = Completer<List<Patient>>();
    await tester.pumpWidget(GuardianApp(api: api));
    await tester.pump();
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
    await tester.tap(find.text('Refresh records'));
    expect(api.patientRequests, 1);
    api.pending!.completeError(const ApiException('Cannot reach the server.'));
    await tester.pumpAndSettle();
    expect(find.text('Could not load records'), findsOneWidget);
    api.pending = null;
    await tester.tap(find.text('Retry'));
    await tester.pumpAndSettle();
    expect(find.text('Patient directory'), findsOneWidget);
    expect(api.patientRequests, 2);
  });
  testWidgets('empty patient state is explicit', (tester) async {
    await tester.pumpWidget(GuardianApp(api: FakeApi()..empty = true));
    await tester.pumpAndSettle();
    expect(find.text('No patients recorded yet.'), findsOneWidget);
  });
  testWidgets('triage review records a real decision and clears queue', (
    tester,
  ) async {
    final api = FakeApi();
    await tester.pumpWidget(GuardianApp(api: api));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Triage review').first);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Record doctor review'));
    await tester.pumpAndSettle();
    expect(api.reviewRequests, 1);
    expect(find.text('Review queue is clear'), findsOneWidget);
  });
  for (final width in [360.0, 1440.0]) {
    testWidgets('detail is responsive and inference is explicit at $width', (
      tester,
    ) async {
      await tester.binding.setSurfaceSize(Size(width, 1000));
      addTearDown(() => tester.binding.setSurfaceSize(null));
      final api = FakeApi();
      await tester.pumpWidget(GuardianApp(api: api));
      await tester.pumpAndSettle();
      final context = tester.element(find.text('Patient overview'));
      Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => PatientDetailScreen(api: api, id: 1),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('Alex Morgan'), findsOneWidget);
      expect(find.text('Clinical overview'), findsOneWidget);
      expect(api.planRequests, 0);
      expect(tester.takeException(), isNull);
      await tester.ensureVisible(find.text('Generate question plan'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Generate question plan'));
      await tester.pumpAndSettle();
      expect(api.planRequests, 1);
      expect(find.text('1. How are you feeling?'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }
  testWidgets('clinical editor validates before writing', (tester) async {
    final api = FakeApi();
    await tester.pumpWidget(GuardianApp(api: api));
    await tester.pumpAndSettle();
    final context = tester.element(find.text('Patient overview'));
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => PatientDetailScreen(api: api, id: 1),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('Edit clinical profile'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextFormField).first, '-2');
    await tester.tap(find.text('Save record'));
    await tester.pumpAndSettle();
    expect(api.writes, 0);
    expect(find.text('Enter a number between 20.0 and 300.0.'), findsOneWidget);
    await tester.enterText(find.byType(TextFormField).first, '175');
    await tester.tap(find.text('Save record'));
    await tester.pumpAndSettle();
    expect(api.writes, 1);
    expect(find.text('Save record'), findsNothing);
  });
  testWidgets('triage controls fit narrow screens', (tester) async {
    await tester.binding.setSurfaceSize(const Size(360, 1000));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    final api = FakeApi();
    await tester.pumpWidget(GuardianApp(api: api));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Triage review').first);
    await tester.pumpAndSettle();
    expect(find.text('Persistent symptoms'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.ensureVisible(find.text('Record doctor review'));
    await tester.tap(find.text('Record doctor review'));
    await tester.pumpAndSettle();
    expect(api.reviewRequests, 1);
    expect(tester.takeException(), isNull);
  });
  testWidgets('manual vital validates and refreshes after saving', (
    tester,
  ) async {
    final api = FakeApi();
    await tester.pumpWidget(GuardianApp(api: api));
    await tester.pumpAndSettle();
    final context = tester.element(find.text('Patient overview'));
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => PatientDetailScreen(api: api, id: 1),
      ),
    );
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Record a vital'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Record a vital'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextFormField).first, 'NaN');
    await tester.tap(find.text('Save record'));
    await tester.pumpAndSettle();
    expect(api.writes, 0);
    await tester.enterText(find.byType(TextFormField).first, '120');
    await tester.tap(find.text('Save record'));
    await tester.pumpAndSettle();
    expect(api.writes, 1);
    expect(find.text('Save record'), findsNothing);
  });
}
