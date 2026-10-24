import 'dart:async';
import 'dart:convert';
import 'package:http/http.dart' as http;
import '../models/follow_up_plan.dart';

abstract interface class PlanApi {
  Future<List<FollowUpPlan>> plans();
  Future<List<PlanPatient>> patients();
  Future<FollowUpPlan> plan(int id);
  Future<FollowUpPlan> draft(int doctorId, int patientId, String instructions);
  Future<void> edit(int id, PlanJson changes);
  Future<void> action(int id, String action);
}

class PlanApiException implements Exception {
  final String message;
  const PlanApiException(this.message);
  @override
  String toString() => message;
}

class HttpPlanApi implements PlanApi {
  final http.Client client;
  final Uri base;
  final String apiKey;
  HttpPlanApi({
    required this.client,
    required String baseUrl,
    required this.apiKey,
  }) : base = Uri.parse(baseUrl.endsWith('/') ? baseUrl : '$baseUrl/');

  Future<dynamic> request(String method, String path, [PlanJson? body]) async {
    try {
      final req = http.Request(method, base.resolve(path));
      req.headers.addAll({'Accept': 'application/json', 'X-API-Key': apiKey});
      if (body != null) {
        req.headers['Content-Type'] = 'application/json';
        req.body = jsonEncode(body);
      }
      final res = await (() async => http.Response.fromStream(
        await client.send(req),
      ))().timeout(const Duration(seconds: 90));
      if (res.statusCode < 200 || res.statusCode >= 300) {
        String detail = '';
        try {
          detail = '${(jsonDecode(res.body) as Map)['detail'] ?? ''}';
        } catch (_) {
          /* Non-JSON proxy response. */
        }
        throw PlanApiException('Request failed (${res.statusCode}). $detail');
      }
      return res.body.isEmpty ? null : jsonDecode(res.body);
    } on TimeoutException {
      throw const PlanApiException(
        'Server timed out. Refresh before retrying: the action may have completed.',
      );
    } on http.ClientException {
      throw const PlanApiException(
        'Cannot reach the server. Check the address and connection.',
      );
    } on FormatException {
      throw const PlanApiException(
        'The server returned an unreadable response.',
      );
    }
  }

  @override
  Future<List<FollowUpPlan>> plans() async =>
      (await request('GET', 'follow-up-plans') as List)
          .map((e) => FollowUpPlan(Map<String, dynamic>.from(e as Map)))
          .toList();
  @override
  Future<List<PlanPatient>> patients() async =>
      (await request('GET', 'patients') as List)
          .map((e) => PlanPatient.fromJson(Map<String, dynamic>.from(e as Map)))
          .toList();
  @override
  Future<FollowUpPlan> plan(int id) async => FollowUpPlan(
    Map<String, dynamic>.from(
      await request('GET', 'follow-up-plans/$id') as Map,
    ),
  );
  @override
  Future<FollowUpPlan> draft(
    int doctorId,
    int patientId,
    String instructions,
  ) async => FollowUpPlan(
    Map<String, dynamic>.from(
      await request('POST', 'follow-up-plans/draft', {
            'doctor_id': doctorId,
            'patient_id': patientId,
            'instructions': instructions,
          })
          as Map,
    ),
  );
  @override
  Future<void> edit(int id, PlanJson changes) async {
    await request('PATCH', 'follow-up-plans/$id', changes);
  }

  @override
  Future<void> action(int id, String action) async {
    await request('POST', 'follow-up-plans/$id/$action');
  }
}
