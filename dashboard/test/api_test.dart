import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:guardian_dashboard/services/dashboard_api.dart';

void main() {
  test('parses sparse patients and sorts by risk, not server order', () async {
    final api = HttpDashboardApi(
      client: MockClient(
        (_) async => http.Response(
          jsonEncode([
            {'id': 1, 'name': 'Low', 'risk_level': 'low'},
            {
              'id': 2,
              'name': 'High',
              'risk_level': 'high',
              'chronic_conditions': null,
            },
            {'id': 3, 'name': 'Unknown'},
          ]),
          200,
        ),
      ),
    );
    final patients = await api.patients();
    expect(patients.map((p) => p.id), [2, 1, 3]);
    expect(patients.first.conditions, isEmpty);
    expect(patients.first.vitals, isEmpty);
    expect(patients.last.risk, 'unknown');
  });
  test('rejects HTTP errors without exposing server response data', () async {
    final api = HttpDashboardApi(
      client: MockClient(
        (_) async => http.Response('sensitive server traceback', 500),
      ),
    );
    await expectLater(
      api.patients(),
      throwsA(
        isA<ApiException>()
            .having((e) => e.statusCode, 'status', 500)
            .having((e) => e.message, 'message', isNot(contains('sensitive'))),
      ),
    );
  });
  test('handles malformed JSON, schema and network failures', () async {
    for (final body in [
      'not json',
      '{}',
      '[{"name":"Missing id"}]',
      '[{"id":1,"name":"Patient","chronic_conditions":[42]}]',
    ]) {
      final api = HttpDashboardApi(
        client: MockClient((_) async => http.Response(body, 200)),
      );
      await expectLater(api.patients(), throwsA(isA<ApiException>()));
    }
    final api = HttpDashboardApi(
      client: MockClient((_) async => throw http.ClientException('offline')),
    );
    await expectLater(api.patients(), throwsA(isA<ApiException>()));
  });
  test('times out the entire streamed response', () async {
    final api = HttpDashboardApi(
      client: MockClient((_) async {
        await Future<void>.delayed(const Duration(milliseconds: 50));
        return http.Response('[]', 200);
      }),
      timeout: const Duration(milliseconds: 1),
    );
    await expectLater(
      api.patients(),
      throwsA(
        isA<ApiException>().having(
          (e) => e.message,
          'message',
          contains('too long'),
        ),
      ),
    );
  });
  test('preserves API base prefix and sends actual review payload', () async {
    final api = HttpDashboardApi(
      baseUrl: 'https://example.org/api',
      client: MockClient((request) async {
        expect(
          request.url.toString(),
          'https://example.org/api/triage/7/review',
        );
        expect(request.method, 'POST');
        expect(jsonDecode(request.body), {
          'confirmed': true,
          'override_action': 'doctor_callback',
        });
        return http.Response('{}', 200);
      }),
    );
    await api.review(7, overrideAction: 'doctor_callback');
  });
  test(
    'clinical endpoints parse plan and never attach a fake call session',
    () async {
      final api = HttpDashboardApi(
        client: MockClient((request) async {
          if (request.method == 'GET') {
            return http.Response('{"question_plan":["How are you?"]}', 200);
          }
          expect(jsonDecode(request.body), {
            'transcript': 'Patient feels well',
          });
          return http.Response(
            '{"analysis":{"summary":"Stable"},"saved":null}',
            200,
          );
        }),
      );
      expect(await api.questionPlan(3), ['How are you?']);
      expect(await api.analyze(3, 'Patient feels well'), {'summary': 'Stable'});
    },
  );
  test(
    'patient edit and vital write use separate endpoints and manual source',
    () async {
      final requests = <http.Request>[];
      final api = HttpDashboardApi(
        client: MockClient((request) async {
          requests.add(request);
          return http.Response('{}', 200);
        }),
      );
      await api.updatePatient(1, {'risk_level': 'medium'});
      await api.addVital(1, {'type': 'weight', 'value': 72, 'unit': 'kg'});
      expect(requests.first.method, 'PATCH');
      expect(requests.first.url.path, '/patients/1');
      expect(requests.last.url.path, '/patients/1/vitals');
      expect(jsonDecode(requests.last.body)['source'], 'manual');
    },
  );
  test('invalid question entries fail at the API boundary', () async {
    final api = HttpDashboardApi(
      client: MockClient(
        (_) async => http.Response('{"question_plan":[42]}', 200),
      ),
    );
    await expectLater(api.questionPlan(1), throwsA(isA<ApiException>()));
  });
}
