import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:http/http.dart' as http;
import 'package:http_parser/http_parser.dart';

class PhotoRequest {
  PhotoRequest.fromJson(Map<String, dynamic> json)
    : id = json['id'] as String,
      instructions = json['instructions'] as String,
      completed = json['status'] == 'completed',
      quality =
          (json['quality'] as Map?)?['status'] as String? ??
          'waiting_for_upload',
      feedback = List<String>.from(
        (json['quality'] as Map?)?['technical_feedback'] ?? [],
      );

  final String id;
  final String instructions;
  final bool completed;
  final String quality;
  final List<String> feedback;
  bool get canRetake =>
      completed &&
      ['retake_recommended', 'uncertain', 'failed'].contains(quality);
  String get qualityLabel => switch (quality) {
    'checking' => 'Checking photo quality…',
    'clear_enough' => 'Photo is clear enough',
    'retake_recommended' => 'A clearer photo may help',
    'uncertain' => 'Quality could not be confirmed',
    'failed' => 'Quality check unavailable',
    _ => completed ? 'Photo received' : 'Photo requested',
  };
}

class CareApiException implements Exception {
  const CareApiException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// Only patient-scoped bearer credentials are accepted; never service API keys.
class CareApi extends ChangeNotifier {
  static const defaultBaseUrl = String.fromEnvironment(
    'BACKEND_BASE_URL',
    defaultValue: 'https://medicall.nulkode.dev',
  );
  final _storage = const FlutterSecureStorage();
  final _client = http.Client();
  String baseUrl = defaultBaseUrl;
  String? _token;
  String? patientName;
  String? expiresAt;
  bool ready = false;
  bool needsPairing = true;
  String? error;
  List<PhotoRequest> requests = [];
  bool _disposed = false;
  void _changed() {
    if (!_disposed) notifyListeners();
  }

  static String newKey() => List.generate(
    24,
    (_) => Random.secure().nextInt(256).toRadixString(16).padLeft(2, '0'),
  ).join();

  Future<void> initialize() async {
    try {
      final saved = await _storage.read(key: 'patient_session');
      if (saved != null) {
        final session = jsonDecode(saved) as Map<String, dynamic>;
        baseUrl = session['base_url'] as String;
        _token = session['token'] as String;
        needsPairing = false;
        await refresh();
      }
    } catch (e) {
      error = e is CareApiException
          ? e.message
          : 'Could not open the saved connection. Please retry or pair again.';
    } finally {
      ready = true;
      _changed();
    }
  }

  Uri _uri(String path) => Uri.parse('$baseUrl$path');
  Map<String, String> get _headers => {'Authorization': 'Bearer $_token'};

  Future<dynamic> _send(http.BaseRequest request) async {
    request.headers.addAll(_headers);
    request.followRedirects = false;
    try {
      final response = await (() async {
        final streamed = await _client.send(request);
        return http.Response.fromStream(streamed);
      })().timeout(const Duration(seconds: 45));
      if (response.statusCode == 401 || response.statusCode == 403) {
        needsPairing = true;
        requests = [];
        patientName = null;
        _changed();
        throw const CareApiException(
          'Your patient connection has expired or was revoked. Ask your care team for a new pairing token.',
        );
      }
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw CareApiException(switch (response.statusCode) {
          404 => 'This request is no longer available.',
          409 => 'The request changed. Refresh its status before trying again.',
          413 => 'The photo is too large. Choose a smaller photo.',
          415 =>
            'Choose a JPEG or PNG photo. This image format is not supported.',
          422 =>
            'The server could not accept this photo or information. Check your selection.',
          _ =>
            'The care server could not complete this action (${response.statusCode}). Please retry.',
        });
      }
      return response.body.isEmpty ? null : jsonDecode(response.body);
    } on TimeoutException {
      throw const CareApiException(
        'The connection timed out. Refresh to check whether the action arrived before retrying.',
      );
    } on http.ClientException {
      throw const CareApiException(
        'Cannot reach your care server. Check your connection and retry.',
      );
    } on FormatException {
      throw const CareApiException(
        'The care server returned an unexpected response.',
      );
    }
  }

  Future<void> pair(String url, String token) async {
    final uri = Uri.tryParse(url.trim());
    if (uri == null ||
        uri.scheme != 'https' ||
        uri.host.isEmpty ||
        uri.userInfo.isNotEmpty ||
        uri.hasQuery ||
        uri.hasFragment) {
      throw const CareApiException(
        'Enter an HTTPS server address without credentials or query parameters.',
      );
    }
    if (token.trim().isEmpty || token.trim().contains(RegExp(r'\s'))) {
      throw const CareApiException(
        'Enter the patient pairing token supplied by your care team.',
      );
    }
    baseUrl = url.trim().replaceFirst(RegExp(r'/+$'), '');
    _token = token.trim();
    final me =
        await _send(http.Request('GET', _uri('/me'))) as Map<String, dynamic>;
    await _storage.write(
      key: 'patient_session',
      value: jsonEncode({'base_url': baseUrl, 'token': _token}),
    );
    patientName = me['name'] as String;
    expiresAt = me['credential_expires_at'] as String?;
    needsPairing = false;
    error = null;
    _changed();
    await refresh();
  }

  Future<void> refresh() async {
    if (_token == null) return;
    final me =
        await _send(http.Request('GET', _uri('/me'))) as Map<String, dynamic>;
    final list =
        await _send(http.Request('GET', _uri('/me/photo-requests'))) as List;
    patientName = me['name'] as String;
    expiresAt = me['credential_expires_at'] as String?;
    requests = list
        .map((e) => PhotoRequest.fromJson(e as Map<String, dynamic>))
        .toList();
    error = null;
    _changed();
  }

  Future<PhotoRequest> request(String id) async => PhotoRequest.fromJson(
    await _send(
          http.Request(
            'GET',
            _uri('/me/photo-requests/${Uri.encodeComponent(id)}'),
          ),
        )
        as Map<String, dynamic>,
  );

  Future<void> upload(String id, Uint8List bytes, String key) async {
    final jpeg =
        bytes.length >= 3 &&
        bytes[0] == 0xff &&
        bytes[1] == 0xd8 &&
        bytes[2] == 0xff;
    final png =
        bytes.length >= 8 &&
        listEquals(bytes.sublist(0, 8), [137, 80, 78, 71, 13, 10, 26, 10]);
    if (!jpeg && !png) {
      throw const CareApiException(
        'Choose a JPEG or PNG photo. This image format is not supported.',
      );
    }
    final request = http.MultipartRequest(
      'POST',
      _uri('/me/photo-requests/${Uri.encodeComponent(id)}/photos'),
    );
    request.headers['Idempotency-Key'] = key;
    request.files.add(
      http.MultipartFile.fromBytes(
        'file',
        bytes,
        filename: png ? 'photo.png' : 'photo.jpg',
        contentType: MediaType('image', png ? 'png' : 'jpeg'),
      ),
    );
    await _send(request);
  }

  Future<void> registerDevice(String? token) async {
    if (needsPairing || _token == null) return;
    var device = await _storage.read(key: 'patient_device_id');
    if (device == null) {
      device = newKey();
      await _storage.write(key: 'patient_device_id', value: device);
    }
    final request = http.Request('PUT', _uri('/me/devices/$device'));
    request.headers['Content-Type'] = 'application/json';
    request.body = jsonEncode({
      'fcm_token': token,
      'platform': kIsWeb
          ? 'web'
          : defaultTargetPlatform == TargetPlatform.iOS
          ? 'ios'
          : 'android',
    });
    await _send(request);
  }

  Future<void> disconnect() async {
    // Do not claim a successful disconnect while a push binding remains live.
    await registerDevice(null);
    await _storage.delete(key: 'patient_session');
    _token = null;
    patientName = null;
    requests = [];
    needsPairing = true;
    _changed();
  }

  @override
  void dispose() {
    _disposed = true;
    _client.close();
    super.dispose();
  }
}
