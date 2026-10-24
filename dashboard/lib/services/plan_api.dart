import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';
import 'package:http/http.dart' as http;
import '../models/follow_up_plan.dart';

abstract interface class PlanApi {
  Future<List<FollowUpPlan>> plans();
  Future<List<PlanPatient>> patients();
  Future<PlanPatient> createPatient(int doctorId, String name, String phone);
  Future<PlanJson> pairPatient(int doctorId, int patientId);
  Future<Uint8List> photoBytes(int planId, String photoId);
  Future<FollowUpPlan> plan(int id);
  Future<FollowUpPlan> draft(int doctorId, int patientId, String instructions);
  Future<void> edit(int id, PlanJson changes);
  Future<PlanJson?> action(int id, String action, {PlanJson? body});
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
  Future<PlanPatient> createPatient(
    int doctorId,
    String name,
    String phone,
  ) async {
    final response = await request('POST', 'patients', {
      'doctor_id': doctorId,
      'name': name,
      'phone': phone,
    });
    return PlanPatient.fromJson(Map<String, dynamic>.from(response as Map));
  }

  @override
  Future<PlanJson> pairPatient(int doctorId, int patientId) async {
    final response = await request(
      'POST',
      'patients/$patientId/pairing-token',
      {'doctor_id': doctorId},
    );
    return Map<String, dynamic>.from(response as Map);
  }

  @override
  Future<Uint8List> photoBytes(int planId, String photoId) async {
    final response = await client
        .get(
          base.resolve(
            'follow-up-plans/$planId/photos/${Uri.encodeComponent(photoId)}/content',
          ),
          headers: {'X-API-Key': apiKey},
        )
        .timeout(const Duration(seconds: 30));
    if (response.statusCode != 200) {
      throw const PlanApiException('Photo unavailable. Refresh and retry.');
    }
    return response.bodyBytes;
  }

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
  Future<PlanJson?> action(int id, String action, {PlanJson? body}) async {
    final response = await request('POST', 'follow-up-plans/$id/$action', body);
    final dispatch = (response as Map?)?['call_dispatch'];
    return dispatch == null ? null : Map<String, dynamic>.from(dispatch as Map);
  }
}
