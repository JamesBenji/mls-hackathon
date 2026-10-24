import 'dart:convert';
import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:guardian_dashboard/medicall_app.dart';
import 'package:guardian_dashboard/services/plan_api.dart';
import 'package:guardian_dashboard/services/sample_plan_api.dart';
import 'package:guardian_dashboard/models/follow_up_plan.dart';
import 'package:guardian_dashboard/widgets/plan_results.dart';

class ControlledPlanApi extends SamplePlanApi {
  Completer<List<FollowUpPlan>>? pending;
  int reads = 0;
  @override
  Future<List<FollowUpPlan>> plans() async {
    reads++;
    if (pending != null) return pending!.future;
    return super.plans();
  }
}

void main() {
  test('numeric series retain gaps and separate revisions and units', () {
    final plan = FollowUpPlan({
      'tracked_items': [
        {
          'id': 'custom',
          'label': 'Custom measure',
          'type': 'number',
          'unit': 'cm',
        },
      ],
      'observations': [
        for (final row in [
          [1, 'cm', 4, 'reported'],
          [1, 'cm', null, 'missing'],
          [1, 'cm', 6, 'reported'],
          [1, 'in', 2, 'reported'],
          [2, 'cm', 9, 'reported'],
        ].indexed)
          {
            'item_id': 'custom',
            'plan_revision': row.$2[0],
            'unit': row.$2[1],
            'value': row.$2[2],
            'status': row.$2[3],
            'reported_at': '2026-10-24T0${row.$1}:00:00Z',
          },
      ],
    });
    final series = observationSeries(plan, plan.trackedItems.single);
    expect(series.length, 3);
    expect(series.first.numericPoints, [4, null, 6, null]);
    expect(series[1].numericPoints, [null, null, null, 2]);
    expect(series.last.numericPoints, [9]);
  });

  testWidgets(
    'derived plans render different items and latest missing is not stale',
    (tester) async {
      final api = SamplePlanApi();
      final plan = await api.plan(2);
      await tester.pumpWidget(MaterialApp(home: MedicallApp(api: api)));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Active'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Sam Taylor'));
      await tester.pumpAndSettle();
      expect(find.text('Cough episodes'), findsOneWidget);
      expect(find.text('Latest: unclear'), findsOneWidget);
      expect(find.byType(NumericObservationChart), findsOneWidget);
      expect(find.text('Wound pain'), findsNothing);
      expect(plan.trackedItems.map((e) => e.type), [
        'number',
        'choice',
        'text',
      ]);
      expect(tester.takeException(), isNull);
    },
  );
  testWidgets('loading locks refresh and errors offer retry', (tester) async {
    final api = ControlledPlanApi()..pending = Completer<List<FollowUpPlan>>();
    await tester.pumpWidget(MedicallApp(api: api));
    await tester.pump();
    await tester.tap(find.text('Refresh'));
    expect(api.reads, 1);
    api.pending!.completeError(const PlanApiException('Server unavailable'));
    await tester.pumpAndSettle();
    expect(find.text('Server unavailable'), findsOneWidget);
    api.pending = null;
    await tester.tap(find.text('Retry / refresh'));
    await tester.pumpAndSettle();
    expect(find.text('Alex Morgan'), findsOneWidget);
  });

  testWidgets('edit saves a draft without activation', (tester) async {
    await tester.binding.setSurfaceSize(const Size(1440, 1100));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    final api = SamplePlanApi();
    await tester.pumpWidget(MedicallApp(api: api));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Alex Morgan'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Edit plan'));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byType(TextFormField).first,
      'Ask about pain twice daily.',
    );
    await tester.scrollUntilVisible(
      find.text('Save changes'),
      500,
      scrollable: find.byType(Scrollable).last,
    );
    await tester.tap(find.text('Save changes'));
    await tester.pumpAndSettle();
    expect((await api.plan(1)).instructions, 'Ask about pain twice daily.');
    expect((await api.plan(1)).status, 'draft');
  });
  test(
    'sample edits and activation are local; call creates no fake result',
    () async {
      final api = SamplePlanApi();
      final plan = await api.draft(7, 2, 'Ask about pain.');
      expect(plan.status, 'draft');
      await api.edit(plan.id, {
        'questions': ['Pain today?'],
      });
      expect((await api.plan(plan.id)).status, 'draft');
      await api.action(plan.id, 'activate');
      expect((await api.plan(plan.id)).status, 'active');
      await api.action(plan.id, 'call');
      expect((await api.plan(plan.id)).calls, isEmpty);
      await api.action(plan.id, 'pause');
      expect((await api.plan(plan.id)).needsReview, isTrue);
      await api.action(plan.id, 'finish');
      expect((await api.plan(plan.id)).status, 'finished');
    },
  );

  test(
    'HTTP contract preserves prefix, key, payloads and clarification',
    () async {
      final requests = <http.Request>[];
      final client = MockClient((req) async {
        requests.add(req);
        if (req.url.path.endsWith('/draft')) {
          return http.Response(
            jsonEncode({'detail': 'Which local call time?'}),
            422,
          );
        }
        return http.Response('', 204);
      });
      final api = HttpPlanApi(
        client: client,
        baseUrl: 'https://example.test/api',
        apiKey: 'session-key',
      );
      await expectLater(
        api.draft(7, 2, 'Follow up'),
        throwsA(
          isA<PlanApiException>().having(
            (e) => e.message,
            'clarification',
            contains('Which local call time?'),
          ),
        ),
      );
      expect(requests.single.url.path, '/api/follow-up-plans/draft');
      expect(requests.single.headers['X-API-Key'], 'session-key');
      expect(jsonDecode(requests.single.body), {
        'doctor_id': 7,
        'patient_id': 2,
        'instructions': 'Follow up',
      });
      await api.edit(4, {
        'questions': ['Pain?'],
      });
      expect(requests.last.method, 'PATCH');
      expect(requests.last.url.path, '/api/follow-up-plans/4');
      await api.action(4, 'activate');
      expect(requests.last.url.path, '/api/follow-up-plans/4/activate');
    },
  );

  for (final width in [360.0, 1440.0]) {
    testWidgets('follow-up review fits ${width.toInt()}px', (tester) async {
      await tester.binding.setSurfaceSize(Size(width, 1100));
      addTearDown(() => tester.binding.setSurfaceSize(null));
      await tester.pumpWidget(MedicallApp(api: SamplePlanApi()));
      await tester.pumpAndSettle();
      expect(find.text('Follow-up plans'), findsOneWidget);
      await tester.tap(find.text('Alex Morgan'));
      await tester.pumpAndSettle();
      expect(find.text('Doctor instructions'), findsOneWidget);
      expect(find.text('Phone: +1 202 555 0142'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets(
    'activation and Call now require confirmation; sample adds no calls',
    (tester) async {
      await tester.binding.setSurfaceSize(const Size(1440, 1100));
      addTearDown(() => tester.binding.setSurfaceSize(null));
      final api = SamplePlanApi();
      await tester.pumpWidget(MedicallApp(api: api));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Alex Morgan'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Activate plan'));
      await tester.pumpAndSettle();
      expect((await api.plan(1)).status, 'draft');
      await tester.tap(find.text('Confirm activation'));
      await tester.pumpAndSettle();
      expect((await api.plan(1)).status, 'active');
      await tester.tap(find.text('Call now'));
      await tester.pumpAndSettle();
      expect(find.text('Confirm call'), findsOneWidget);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect((await api.plan(1)).calls.length, 5);
      await tester.tap(find.text('Call now'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Confirm call'));
      await tester.pumpAndSettle();
      expect(find.text('Preview only — no call placed.'), findsOneWidget);
      expect((await api.plan(1)).calls.length, 5);
    },
  );
}
