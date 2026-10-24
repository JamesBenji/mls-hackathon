import '../models/follow_up_plan.dart';
import 'plan_api.dart';

/// Offline fixtures only. No HTTP client, network imports or calling capability.
/// Interpretation regeneration is a static mapping, not a Gemma invocation.
class SamplePlanApi implements PlanApi {
  final Map<int, FollowUpPlan> _plans = {};
  final List<PlanPatient> identities = const [
    PlanPatient(1, 'Alex Morgan', '+1 202 555 0142', 'America/New_York'),
    PlanPatient(2, 'Sam Taylor', '+1 202 555 0189', 'America/New_York'),
  ];
  SamplePlanApi() {
    _plans[1] = _fixture(
      1,
      'Call daily for one week. Ask about wound pain on a 0–10 scale, redness and request a wound photo on the first call.',
      'draft',
    );
    _plans[2] = _fixture(
      2,
      'Call daily to track cough episodes, sleep quality and how breathing feels in the patient’s own words.',
      'active',
    );
  }

  PlanJson _interpret(String instructions) {
    final respiratory = RegExp(
      'cough|breath|sleep',
      caseSensitive: false,
    ).hasMatch(instructions);
    return {
      'context': respiratory
          ? 'Follow-up on cough, sleep quality and changes in breathing.'
          : 'Follow-up on wound pain, redness and the requested wound photo.',
      'questions': respiratory
          ? [
              'How many coughing episodes?',
              'How was your sleep?',
              'How does your breathing feel?',
            ]
          : [
              'What is your wound pain from 0 to 10?',
              'Any redness?',
              'Can you share the requested photo?',
            ],
      'tracked_items': respiratory
          ? [
              {
                'id': 'cough',
                'label': 'Cough episodes',
                'type': 'number',
                'unit': 'episodes',
                'min': 0,
                'max': null,
              },
              {
                'id': 'sleep',
                'label': 'Sleep quality',
                'type': 'choice',
                'options': ['Poor', 'Fair', 'Good'],
              },
              {
                'id': 'breathing',
                'label': 'Breathing in patient’s words',
                'type': 'text',
              },
            ]
          : [
              {
                'id': 'pain',
                'label': 'Wound pain',
                'type': 'number',
                'unit': 'score /10',
                'min': 0,
                'max': 10,
              },
              {'id': 'redness', 'label': 'Redness reported', 'type': 'boolean'},
              {'id': 'wound_photo', 'label': 'Wound photo', 'type': 'photo'},
            ],
      'photo_policy': respiratory ? 'none' : 'first_call',
    };
  }

  FollowUpPlan _fixture(int id, String instructions, String status) {
    final interpretation = _interpret(instructions);
    final observations = <PlanJson>[];
    final calls = <PlanJson>[];
    for (var i = 0; i < 5; i++) {
      final at = '2026-10-${20 + i}T13:00:00';
      final callId = 'call-$id-$i';
      calls.add({
        'id': callId,
        'call_session_id': callId,
        'started_at': at,
        'status': 'completed',
        'summary': i == 2
            ? 'One numeric answer was not reported.'
            : 'Follow-up answers recorded; review the source quotes.',
        'concerns': [],
      });
      for (final raw in interpretation['tracked_items'] as List) {
        final item = raw as PlanJson;
        final missing = i == 2 && item['type'] == 'number';
        final value = switch (item['id']) {
          'pain' => [7, 6, null, 4, 3][i],
          'redness' => i < 2,
          'wound_photo' => null,
          'cough' => [12, 9, null, 7, 5][i],
          'sleep' => ['Poor', 'Fair', 'Fair', 'Good', 'Good'][i],
          _ => [
            'Breathing feels tight.',
            'A little easier.',
            'About the same.',
            'More comfortable today.',
            'I can walk around more easily.',
          ][i],
        };
        observations.add({
          'id': '$callId-${item['id']}',
          'item_id': item['id'],
          'plan_revision': 1,
          'definition_snapshot': Map<String, dynamic>.from(item),
          'call_session_id': callId,
          'value': value,
          'unit': item['unit'],
          'status': missing
              ? 'missing'
              : item['type'] == 'photo'
              ? 'declined'
              : 'reported',
          'source_quote': missing
              ? 'I did not keep count today.'
              : item['type'] == 'photo'
              ? 'I cannot send a photo right now.'
              : '$value',
          'reported_at': at,
          'measured_at': item['type'] == 'number' && !missing
              ? '2026-10-${20 + i}T12:30:00'
              : null,
        });
      }
    }
    // The latest missing answer must not be replaced with an earlier value.
    if (id == 2) {
      observations.add({
        'id': 'call-2-latest-missing',
        'item_id': 'cough',
        'plan_revision': 1,
        'definition_snapshot': (interpretation['tracked_items'] as List).first,
        'call_session_id': 'call-2-latest',
        'value': null,
        'unit': 'episodes',
        'status': 'unclear',
        'source_quote': 'I am not sure how often today.',
        'reported_at': '2026-10-24T16:00:00',
        'measured_at': null,
      });
    }
    return FollowUpPlan({
      'id': id,
      'doctor_id': 1,
      'patient_id': id,
      'patient_name': identities[id - 1].name,
      'instructions': instructions,
      ...interpretation,
      'revision': 1,
      'status': status,
      'schedule': {
        'timezone': 'America/New_York',
        'local_time': '09:00',
        'frequency': 'daily',
        'start_date': '2026-10-24',
        'end_date': '2026-10-31',
      },
      'next_call_at': null,
      'last_summary': null,
      'calls': calls,
      'observations': observations,
    });
  }

  @override
  Future<List<FollowUpPlan>> plans() async => _plans.values.toList();
  @override
  Future<List<PlanPatient>> patients() async => identities;
  @override
  Future<FollowUpPlan> plan(int id) async => _plans[id]!;
  @override
  Future<FollowUpPlan> draft(
    int doctorId,
    int patientId,
    String instructions,
  ) async {
    final id = _plans.length + 1;
    final identity = identities.firstWhere((p) => p.id == patientId);
    final today = DateTime.now().toIso8601String().substring(0, 10);
    return _plans[id] = FollowUpPlan({
      'id': id,
      'doctor_id': doctorId,
      'patient_id': patientId,
      'patient_name': identity.name,
      'instructions': instructions,
      ..._interpret(instructions),
      'revision': 1,
      'status': 'draft',
      'calls': [],
      'observations': [],
      'schedule': {
        'timezone': identity.timezone,
        'local_time': '09:00',
        'frequency': 'once',
        'start_date': today,
        'end_date': today,
      },
    });
  }

  @override
  Future<void> edit(int id, PlanJson changes) async {
    final old = _plans[id]!;
    final changedInstructions =
        changes.containsKey('instructions') &&
        changes['instructions'] != old.instructions;
    _plans[id] = FollowUpPlan({
      ...old.data,
      ...changes,
      if (changedInstructions) ..._interpret(changes['instructions'] as String),
      if (changes.isNotEmpty) 'revision': (old.data['revision'] as int) + 1,
      if (changes.isNotEmpty) 'status': 'draft',
    });
  }

  @override
  Future<void> action(int id, String action) async {
    if (action == 'call') return; // Never places or fabricates another call.
    final status = {
      'activate': 'active',
      'pause': 'paused',
      'finish': 'finished',
    }[action];
    if (status == null) {
      throw const PlanApiException('Unsupported preview action.');
    }
    _plans[id] = FollowUpPlan({..._plans[id]!.data, 'status': status});
  }
}
