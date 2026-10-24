import 'dart:async';
import 'dart:convert';
import 'package:http/http.dart' as http;
import '../models/patient.dart';

const apiBase = String.fromEnvironment(
  'API_BASE',
  defaultValue: 'http://localhost:8000',
);

abstract interface class DashboardApi {
  Future<List<Patient>> patients();
  Future<Patient> patient(int id);
  Future<List<TriageDecision>> triage();
  Future<void> review(int id, {String? overrideAction});
  Future<void> updatePatient(int id, Json changes);
  Future<void> addVital(int id, Json vital);
  Future<List<String>> questionPlan(int id);
  Future<Json> analyze(int id, String transcript);
}

class ApiException implements Exception {
  final String message;
  final int? statusCode;
  const ApiException(this.message, [this.statusCode]);
  @override
  String toString() => message;
}

class HttpDashboardApi implements DashboardApi {
  final http.Client client;
  final Uri base;
  final Duration timeout;
  HttpDashboardApi({
    required this.client,
    String baseUrl = apiBase,
    this.timeout = const Duration(seconds: 25),
  }) : base = Uri.parse(baseUrl.endsWith('/') ? baseUrl : '$baseUrl/');

  Future<dynamic> _request(
    String method,
    String path, {
    Json? body,
    bool clinical = false,
  }) async {
    try {
      final request = http.Request(method, base.resolve(path));
      request.headers['Accept'] = 'application/json';
      if (body != null) {
        request.headers['Content-Type'] = 'application/json';
        request.body = jsonEncode(body);
      }
      final response = await (() async => http.Response.fromStream(
        await client.send(request),
      ))().timeout(clinical ? const Duration(seconds: 120) : timeout);
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw ApiException(
          'Request failed (${response.statusCode}). Check the server and retry.',
          response.statusCode,
        );
      }
      return jsonDecode(response.body);
    } on ApiException {
      rethrow;
    } on TimeoutException {
      throw const ApiException(
        'The server took too long. Refresh before retrying a write; it may have completed.',
      );
    } on FormatException {
      throw const ApiException('The server returned an unreadable response.');
    } on http.ClientException {
      throw const ApiException(
        'Cannot reach the server. Check your connection and API_BASE.',
      );
    }
  }

  Future<T> _parse<T>(
    Future<dynamic> response,
    T Function(dynamic) parse,
  ) async {
    final json = await response;
    try {
      return parse(json);
    } on TypeError {
      throw const ApiException('The server response has an unexpected format.');
    }
  }

  @override
  Future<List<Patient>> patients() =>
      _parse(_request('GET', 'patients'), (json) {
        final result = objects(json as List).map(Patient.fromJson).toList();
        result.sort((a, b) {
          final rank = b.riskRank.compareTo(a.riskRank);
          return rank != 0 ? rank : a.name.compareTo(b.name);
        });
        return result;
      });
  @override
  Future<Patient> patient(int id) => _parse(
    _request('GET', 'patients/$id'),
    (json) => Patient.fromJson(json as Json),
  );
  @override
  Future<List<TriageDecision>> triage() => _parse(
    _request('GET', 'triage'),
    (json) => objects(json as List).map(TriageDecision.fromJson).toList(),
  );
  @override
  Future<void> review(int id, {String? overrideAction}) async {
    await _request(
      'POST',
      'triage/$id/review',
      body: {
        'confirmed': true,
        if (overrideAction != null) 'override_action': overrideAction,
      },
    );
  }

  @override
  Future<void> updatePatient(int id, Json changes) async {
    await _request('PATCH', 'patients/$id', body: changes);
  }

  @override
  Future<void> addVital(int id, Json vital) async {
    await _request(
      'POST',
      'patients/$id/vitals',
      body: {...vital, 'source': 'manual'},
    );
  }

  @override
  Future<List<String>> questionPlan(int id) => _parse(
    _request('GET', 'clinical/$id/question-plan', clinical: true),
    (json) => ((json as Json)['question_plan'] as List)
        .map((item) => item as String)
        .toList(),
  );
  @override
  Future<Json> analyze(int id, String transcript) => _parse(
    _request(
      'POST',
      'clinical/$id/analyze',
      clinical: true,
      body: {'transcript': transcript},
    ),
    (json) {
      final analysis = (json as Json)['analysis'] as Json;
      // Accept the current nested contract and older flat responses.
      return {
        ...analysis,
        if (analysis['report'] is Map)
          ...Map<String, dynamic>.from(analysis['report'] as Map),
        if (analysis['triage'] is Map)
          ...Map<String, dynamic>.from(analysis['triage'] as Map),
      };
    },
  );
}
