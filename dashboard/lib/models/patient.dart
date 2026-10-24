typedef Json = Map<String, dynamic>;

List<Json> objects(dynamic value) => (value as List? ?? [])
    .map((item) => Map<String, dynamic>.from(item as Map))
    .toList();

class Patient {
  final int id;
  final String name, phone, risk, category;
  final List<String> conditions;
  final Json data;
  final List<Json> medications, vitals, reports;

  Patient.fromJson(this.data)
    : id = data['id'] as int,
      name = data['name'] as String,
      phone = data['phone'] as String? ?? '',
      risk = data['risk_level'] as String? ?? 'unknown',
      category = data['category'] as String? ?? 'Uncategorized',
      conditions = (data['chronic_conditions'] as List? ?? [])
          .map((item) => item as String)
          .toList(),
      medications = objects(data['medications']),
      vitals = objects(data['vitals']),
      reports = objects(data['reports']);

  int get riskRank => switch (risk) {
    'high' => 3,
    'medium' => 2,
    'low' => 1,
    _ => 0,
  };
  String get initials => name.trim().isEmpty
      ? '?'
      : name
            .trim()
            .split(RegExp(r'\s+'))
            .take(2)
            .map((s) => s[0])
            .join()
            .toUpperCase();
}

class TriageDecision {
  final int id, patientId;
  final String urgency, action, rationale;

  TriageDecision.fromJson(Json json)
    : id = json['id'] as int,
      patientId = json['patient_id'] as int,
      urgency = json['urgency'] as String? ?? 'unknown',
      action = json['recommended_action'] as String? ?? 'none',
      rationale = json['rationale'] as String? ?? 'No rationale recorded.';
}
