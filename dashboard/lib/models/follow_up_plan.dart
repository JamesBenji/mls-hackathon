import 'dart:convert';

typedef PlanJson = Map<String, dynamic>;

class PlanPatient {
  final int id;
  final String name, phone, timezone;
  const PlanPatient(this.id, this.name, this.phone, this.timezone);
  factory PlanPatient.fromJson(PlanJson j) => PlanPatient(
    j['id'] as int,
    '${j['name'] ?? j['patient_name'] ?? 'Unnamed patient'}',
    '${j['phone'] ?? j['phone_number'] ?? ''}',
    '${j['timezone'] ?? ''}',
  );
}

class FollowUpPlan {
  final PlanJson data;
  FollowUpPlan(PlanJson data) : data = Map.unmodifiable(data);
  int get id => data['id'] as int;
  int get patientId => data['patient_id'] as int;
  String get name => '${data['patient_name'] ?? 'Patient $patientId'}';
  String get status => '${data['status']}';
  String get instructions => '${data['instructions'] ?? ''}';
  String get context => '${data['context'] ?? ''}';
  String get photoPolicy => '${data['photo_policy'] ?? 'none'}';
  String? get revision =>
      (data['revision'] ?? data['plan_revision'])?.toString();
  List<String> strings(String key) =>
      (data[key] as List? ?? []).map((e) => '$e').toList();
  List<TrackingItem> get trackedItems => (data['tracked_items'] as List? ?? [])
      .map(TrackingItem.fromValue)
      .toList();
  List<PlanObservation> get observations =>
      (data['observations'] as List? ?? [])
          .whereType<Map>()
          .map((e) => PlanObservation(Map<String, dynamic>.from(e)))
          .toList();
  List<TrackingItem> get resultItems {
    final items = {for (final item in trackedItems) item.id: item};
    for (final observation in observations) {
      final snapshot = observation.definition;
      if (snapshot != null) items.putIfAbsent(snapshot.id, () => snapshot);
    }
    return items.values.toList();
  }

  PlanJson get schedule =>
      Map<String, dynamic>.from(data['schedule'] as Map? ?? {});
  List<PlanJson> get calls => (data['calls'] as List? ?? [])
      .whereType<Map>()
      .map((e) => Map<String, dynamic>.from(e))
      .toList();
  bool get needsReview => status == 'draft' || status == 'paused';
}

class TrackingItem {
  final String id, label, type;
  final String? unit;
  final List<String> options;
  final num? min, max;
  TrackingItem(
    this.id,
    this.label,
    this.type,
    this.unit,
    this.options, [
    this.min,
    this.max,
  ]);
  String get signature => jsonEncode([id, type, unit, min, max, options]);
  factory TrackingItem.fromValue(dynamic value) {
    if (value is String) {
      return TrackingItem('legacy:$value', value, 'text', null, []);
    }
    final j = value as Map;
    return TrackingItem(
      '${j['id']}',
      '${j['label']}',
      '${j['type'] ?? 'text'}',
      j['unit'] as String?,
      (j['options'] as List? ?? []).map((e) => '$e').toList(),
      j['min'] as num?,
      j['max'] as num?,
    );
  }
}

class PlanObservation {
  final PlanJson data;
  PlanObservation(this.data);
  String get itemId => '${data['item_id']}';
  String get revision => '${data['plan_revision'] ?? 'unknown'}';
  String? get unit => data['unit'] as String?;
  TrackingItem? get definition => data['definition_snapshot'] is Map
      ? TrackingItem.fromValue(data['definition_snapshot'])
      : null;
  String get status => '${data['status'] ?? 'unclear'}';
  dynamic get value => data['value'];
  num? get number => status == 'reported' && value is num ? value as num : null;
  String get reportedAt => utcTimestamp(data['reported_at']);
  String get measuredAt => utcTimestamp(data['measured_at']);
  String get quote => '${data['source_quote'] ?? 'No source quote provided'}';
  String get callId => '${data['call_session_id'] ?? 'Not provided'}';
  String get valueLabel => status != 'reported'
      ? status
      : value == null
      ? 'unclear'
      : value is bool
      ? (value == true ? 'Yes' : 'No')
      : '$value';
}

/// An individual series never joins different item IDs, revisions, or units.
/// Null points are retained so the chart cannot bridge missing observations.
class ObservationSeries {
  final TrackingItem item;
  final String revision;
  final String? unit;
  final List<PlanObservation> observations;
  ObservationSeries(this.item, this.revision, this.unit, this.observations);
  List<num?> get numericPoints =>
      observations.map((o) => compatible(o) ? o.number : null).toList();
  bool compatible(PlanObservation o) =>
      o.unit == unit &&
      (o.definition == null || o.definition!.signature == item.signature);
  PlanObservation get latest => observations.last;
}

List<ObservationSeries> observationSeries(
  FollowUpPlan plan,
  TrackingItem item,
) {
  final relevant = plan.observations.where((o) => o.itemId == item.id).toList()
    ..sort(compareObservationTime);
  final groups = <String, ObservationSeries>{};
  for (final observation in relevant) {
    final definition = observation.definition ?? item;
    final key = jsonEncode([
      observation.revision,
      observation.unit,
      definition.signature,
    ]);
    groups.putIfAbsent(
      key,
      () => ObservationSeries(
        definition,
        observation.revision,
        observation.unit,
        relevant.where((o) => o.revision == observation.revision).toList(),
      ),
    );
  }
  return groups.values.toList();
}

int compareObservationTime(PlanObservation a, PlanObservation b) {
  final left = parseUtcTimestamp(a.data['reported_at']);
  final right = parseUtcTimestamp(b.data['reported_at']);
  return left == null || right == null
      ? a.reportedAt.compareTo(b.reportedAt)
      : left.compareTo(right);
}

DateTime? parseUtcTimestamp(dynamic value) {
  if (value == null) return null;
  final text = '$value';
  final zoned = RegExp(
    r'(Z|[+-]\d{2}:?\d{2})$',
    caseSensitive: false,
  ).hasMatch(text);
  return DateTime.tryParse(zoned ? text : '${text}Z')?.toUtc();
}

String utcTimestamp(dynamic value) {
  if (value == null) return 'Not provided';
  final time = parseUtcTimestamp(value);
  return time == null
      ? '$value'
      : '${time.toIso8601String().replaceFirst('T', ' ').replaceFirst('.000Z', '').replaceFirst('Z', '')} UTC';
}
