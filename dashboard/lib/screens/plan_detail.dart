import 'package:flutter/material.dart';
import 'package:forui/forui.dart';
import '../models/follow_up_plan.dart';
import '../services/plan_api.dart';
import '../widgets/clinical_widgets.dart';
import '../widgets/plan_results.dart';

class PlanDetail extends StatefulWidget {
  final PlanApi api;
  final FollowUpPlan plan;
  final bool busy;
  final ValueChanged<String> onAction;
  final VoidCallback onEdit;
  const PlanDetail({
    super.key,
    required this.api,
    required this.plan,
    required this.busy,
    required this.onAction,
    required this.onEdit,
  });
  @override
  State<PlanDetail> createState() => _PlanDetailState();
}

class _PlanDetailState extends State<PlanDetail> {
  late Future<List<PlanPatient>> _patients = widget.api.patients();
  Widget _section(String title, Widget child) => Padding(
    padding: const EdgeInsets.only(bottom: 16),
    child: SectionCard(
      title: title,
      child: Material(color: Colors.transparent, child: child),
    ),
  );
  Widget _lines(List<String> lines) => Text(
    lines.isEmpty ? 'Not specified' : lines.map((s) => '• $s').join('\n'),
    style: const TextStyle(height: 1.7),
  );
  String _value(dynamic value) {
    if (value == null || '$value'.isEmpty) return 'Not provided';
    if (value is Map) {
      return value.entries
          .map((e) => '${e.key}: ${_value(e.value)}')
          .join('\n');
    }
    if (value is List) {
      return value.isEmpty ? 'None reported' : value.map(_value).join('\n');
    }
    return '$value';
  }

  @override
  Widget build(BuildContext context) {
    final p = widget.plan;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _section(
          p.name,
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('Plan #${p.id} · ${p.status}'),
              const SizedBox(height: 12),
              FutureBuilder<List<PlanPatient>>(
                future: _patients,
                builder: (context, snapshot) {
                  final matches =
                      snapshot.data
                          ?.where((identity) => identity.id == p.patientId)
                          .toList() ??
                      [];
                  final patient = matches.isEmpty ? null : matches.first;
                  return Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'Phone: ${patient == null || patient.phone.isEmpty ? 'Not available' : patient.phone}',
                      ),
                      Text('Timezone: ${_value(p.schedule['timezone'])}'),
                      if (snapshot.hasError)
                        TextButton(
                          onPressed: () =>
                              setState(() => _patients = widget.api.patients()),
                          child: const Text('Retry patient details'),
                        ),
                      const SizedBox(height: 18),
                      Wrap(
                        spacing: 8,
                        runSpacing: 8,
                        children: [
                          if (p.needsReview)
                            ClinicalButton(
                              onPress: widget.busy ? null : widget.onEdit,
                              variant: FButtonVariant.outline,
                              child: const Text('Edit plan'),
                            ),
                          if (p.needsReview)
                            ClinicalButton(
                              onPress: widget.busy || p.schedule.isEmpty
                                  ? null
                                  : () => widget.onAction('activate'),
                              child: Text(
                                p.status == 'paused'
                                    ? 'Review & reactivate'
                                    : 'Activate plan',
                              ),
                            ),
                          if (p.status == 'active') ...[
                            ClinicalButton(
                              onPress: widget.busy
                                  ? null
                                  : () => widget.onAction('pause'),
                              variant: FButtonVariant.outline,
                              child: const Text('Pause'),
                            ),
                            ClinicalButton(
                              onPress:
                                  widget.busy ||
                                      patient == null ||
                                      patient.phone.isEmpty
                                  ? null
                                  : () => widget.onAction('call'),
                              child: const Text('Call now'),
                            ),
                          ],
                          if (p.status != 'finished')
                            ClinicalButton(
                              onPress: widget.busy
                                  ? null
                                  : () => widget.onAction('finish'),
                              variant: FButtonVariant.ghost,
                              child: const Text('Finish'),
                            ),
                        ],
                      ),
                    ],
                  );
                },
              ),
            ],
          ),
        ),
        if (p.status == 'draft')
          const Padding(
            padding: EdgeInsets.only(bottom: 16),
            child: Text(
              'Draft · Review instructions, tracking and schedule. No calls until you activate.',
            ),
          ),
        _section('Doctor instructions', SelectableText(p.instructions)),
        if (p.schedule.isEmpty)
          const Padding(
            padding: EdgeInsets.only(bottom: 16),
            child: Text(
              'Set the call schedule in Edit plan before activating.',
            ),
          ),
        if (p.context.isNotEmpty)
          _section('AI interpretation · context', SelectableText(p.context)),
        _section(
          'AI interpretation · tracking plan',
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                'Derived from your instructions. To correct the interpretation, edit your instructions and review the regenerated plan.',
              ),
              const SizedBox(height: 12),
              _lines(
                p.trackedItems
                    .map(
                      (item) =>
                          '${item.label}${item.unit == null ? '' : ' (${item.unit})'}${item.options.isEmpty ? '' : ' · ${item.options.join(', ')}'}',
                    )
                    .toList(),
              ),
              const SizedBox(height: 16),
              const Text(
                'Suggested questions',
                style: TextStyle(fontWeight: FontWeight.w600),
              ),
              _lines(p.strings('questions')),
            ],
          ),
        ),
        _section(
          'Photos & schedule',
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'Photos: ${switch (p.photoPolicy) {
                  'first_call' => 'First call',
                  'every_call' => 'Every call',
                  _ => 'None',
                }}',
              ),
              const SizedBox(height: 10),
              Text(
                '${_value(p.schedule['frequency'])} at ${_value(p.schedule['local_time'])} · ${_value(p.schedule['timezone'])}',
              ),
              Text(
                '${_value(p.schedule['start_date'])} → ${_value(p.schedule['end_date'])}',
              ),
              const SizedBox(height: 10),
              Text(
                'Next call: ${p.data['next_call_at'] == null ? 'Not scheduled' : utcTimestamp(p.data['next_call_at'])}',
              ),
            ],
          ),
        ),
        Padding(
          padding: const EdgeInsets.only(bottom: 16),
          child: PlanResults(plan: p),
        ),
        _section(
          'Per-call history · AI summaries',
          Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (p.calls.isEmpty)
                Text(
                  p.data['last_summary'] == null
                      ? 'No call results available.'
                      : 'Latest summary: ${p.data['last_summary']}',
                ),
              for (final call in p.calls)
                Padding(
                  padding: const EdgeInsets.only(bottom: 20),
                  child: ExpansionTile(
                    title: Text(
                      '${utcTimestamp(call['started_at'] ?? call['created_at'])} · ${call['status'] ?? 'Status not provided'}',
                    ),
                    expandedCrossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const SizedBox(height: 8),
                      SelectableText('AI summary: ${_value(call['summary'])}'),
                      if (call['results'] != null)
                        SelectableText(
                          'AI interpretation: ${_value(call['results'])}',
                        ),
                      if (call['tracked_answers'] != null)
                        SelectableText(
                          'Tracked answers: ${_value(call['tracked_answers'])}',
                        ),
                      if (call['concerns'] != null)
                        SelectableText('Concerns: ${_value(call['concerns'])}'),
                      if (call['photos'] != null)
                        SelectableText('Photos: ${_value(call['photos'])}'),
                      const SizedBox(height: 12),
                      const Text(
                        'Patient-reported observations',
                        style: TextStyle(fontWeight: FontWeight.w600),
                      ),
                      for (final observation in p.observations.where(
                        (o) =>
                            o.callId ==
                            '${call['call_session_id'] ?? call['id']}',
                      ))
                        ObservationRow(observation: observation),
                    ],
                  ),
                ),
            ],
          ),
        ),
      ],
    );
  }
}
