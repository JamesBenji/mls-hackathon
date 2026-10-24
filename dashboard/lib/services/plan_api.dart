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
  }) : base = _serverBase(baseUrl);

  static Uri _serverBase(String address) {
    var uri = Uri.parse(address.trim());
    if (uri.host == 'medicall.nulkode.dev') {
      uri = uri.replace(scheme: 'https');
    }
    if (!uri.path.endsWith('/')) {
      uri = uri.replace(path: '${uri.path}/');
    }
    return uri;
  }

  Future<http.Response> _send(http.Request request) async {
    var current = request;
    for (var redirects = 0; redirects <= 3; redirects++) {
      current.followRedirects = false;
      final streamed = await client.send(current);
      final response = await http.Response.fromStream(streamed);
      if (response.statusCode != 307 && response.statusCode != 308) {
        return response;
      }

      final location = response.headers['location'];
      if (location == null || redirects == 3) {
        throw const PlanApiException(
          'The server returned an invalid redirect. No automatic retry was made.',
        );
      }
      var destination = current.url.resolve(location);
      // Reverse proxies can generate an HTTP Location for an HTTPS request.
      // Keep transport encrypted rather than following that downgrade.
      if (current.url.scheme == 'https' && destination.scheme == 'http') {
        destination = destination.replace(scheme: 'https');
      }
      final currentPath = current.url.path.replaceFirst(RegExp(r'/+$'), '');
      final destinationPath = destination.path.replaceFirst(RegExp(r'/+$'), '');
      if (destination.origin != current.url.origin ||
          destinationPath != currentPath ||
          destination.query != current.url.query ||
          destination == current.url) {
        throw PlanApiException(
          'Server redirect blocked for ${request.method} '
          '${request.url.origin}${request.url.path} → '
          '${destination.origin}${destination.path}. '
          'No call retry was made. Reconnect using the canonical HTTPS server address.',
        );
      }

      final redirected = http.Request(current.method, destination);
      redirected.headers.addAll(current.headers);
      redirected.bodyBytes = current.bodyBytes;
      current = redirected;
    }
    throw const PlanApiException('Too many server redirects.');
  }

  Future<dynamic> request(
    String method,
    String path, [
    PlanJson? body,
    Duration timeout = const Duration(seconds: 90),
  ]) async {
    try {
      final req = http.Request(method, base.resolve(path));
      req.headers.addAll({'Accept': 'application/json', 'X-API-Key': apiKey});
      if (body != null) {
        req.headers['Content-Type'] = 'application/json';
        req.body = jsonEncode(body);
      }
      final res = await _send(req).timeout(timeout);
      if (res.statusCode < 200 || res.statusCode >= 300) {
        String detail = '';
        try {
          detail = '${(jsonDecode(res.body) as Map)['detail'] ?? ''}';
        } catch (_) {
          /* Non-JSON proxy response. */
        }
        throw PlanApiException(
          'Request failed (${res.statusCode}) for $method '
          '${req.url.origin}${req.url.path}. $detail',
        );
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
          }, const Duration(seconds: 270))
          as Map,
    ),
  );
  @override
  Future<void> edit(int id, PlanJson changes) async {
    await request(
      'PATCH',
      'follow-up-plans/$id',
      changes,
      const Duration(seconds: 270),
    );
  }

  @override
  Future<PlanJson?> action(int id, String action, {PlanJson? body}) async {
    final response = await request('POST', 'follow-up-plans/$id/$action', body);
    final dispatch = (response as Map?)?['call_dispatch'];
    return dispatch == null ? null : Map<String, dynamic>.from(dispatch as Map);
  }
}
