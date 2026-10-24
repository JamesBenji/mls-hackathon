import 'package:flutter/material.dart';
import 'package:forui/forui.dart';
import '../widgets/plan_photos.dart';
import '../models/follow_up_plan.dart';
import '../services/plan_api.dart';
import '../widgets/workspace_widgets.dart';
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

  Widget _section(String title, Widget child) => Padding(
    padding: const EdgeInsets.only(bottom: 12),
    child: SectionCard(title: title, child: child),
  );
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
              Text(
                'Plan #${p.id} · ${p.status} · revision ${p.revision ?? '—'}',
                style: const TextStyle(fontSize: 12, color: Color(0xFF62766F)),
              ),
              const SizedBox(height: 8),
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
                      Wrap(
                        spacing: 20,
                        runSpacing: 4,
                        children: [
                          Text(
                            'Phone: ${patient == null || patient.phone.isEmpty ? 'Not available' : patient.phone}',
                            style: const TextStyle(fontSize: 12),
                          ),
                          Text(
                            'Timezone: ${_value(p.schedule['timezone'])}',
                            style: const TextStyle(fontSize: 12),
                          ),
                        ],
                      ),
                      if (snapshot.hasError)
                        WorkspaceButton(
                          onPress: () =>
                              setState(() => _patients = widget.api.patients()),
                          variant: FButtonVariant.ghost,
                          child: const Text('Retry patient details'),
                        ),
                      const SizedBox(height: 12),
                      Wrap(
                        spacing: 8,
                        runSpacing: 8,
                        children: [
                          if (p.needsReview)
                            WorkspaceButton(
                              onPress: widget.busy ? null : widget.onEdit,
                              variant: FButtonVariant.outline,
                              prefix: const Icon(FLucideIcons.pencil, size: 14),
                              child: const Text('Edit plan'),
                            ),
                          if (p.needsReview)
                            WorkspaceButton(
                              onPress: widget.busy || p.schedule.isEmpty
                                  ? null
                                  : () => widget.onAction('activate'),
                              prefix: const Icon(FLucideIcons.check, size: 14),
                              child: Text(
                                p.status == 'paused'
                                    ? 'Reactivate'
                                    : 'Activate plan',
                              ),
                            ),
                          if (p.status == 'active') ...[
                            WorkspaceButton(
                              onPress: widget.busy
                                  ? null
                                  : () => widget.onAction('pause'),
                              variant: FButtonVariant.outline,
                              prefix: const Icon(FLucideIcons.pause, size: 14),
                              child: const Text('Pause'),
                            ),
                          ],
                          if (p.status != 'finished')
                            WorkspaceButton(
                              onPress:
                                  widget.busy ||
                                      patient == null ||
                                      !RegExp(r'^\+[1-9][0-9]{6,14}$').hasMatch(
                                        patient.phone.replaceAll(' ', ''),
                                      )
                                  ? null
                                  : () => widget.onAction('call'),
                              prefix: const Icon(FLucideIcons.phone, size: 14),
                              child: const Text('Call now'),
                            ),
                          if (p.status != 'finished')
                            WorkspaceButton(
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
        _section(
          'Doctor instructions',
          Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              SelectableText(
                p.instructions,
                style: const TextStyle(fontSize: 13, height: 1.5),
              ),
              const SizedBox(height: 10),
              if (p.schedule.isEmpty)
                const Text(
                  'Set a call schedule in Edit plan before activating.',
                  style: TextStyle(fontSize: 12),
                )
              else
                Wrap(
                  spacing: 16,
                  runSpacing: 4,
                  children: [
                    Text(
                      '${_value(p.schedule['frequency'])} · ${_value(p.schedule['local_time'])} ${_value(p.schedule['timezone'])}',
                      style: const TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                    Text(
                      '${_value(p.schedule['start_date'])} → ${_value(p.schedule['end_date'])}',
                      style: const TextStyle(fontSize: 12),
                    ),
                    Text(
                      'Photos: ${switch (p.photoPolicy) {
                        'first_call' => 'First call',
                        'every_call' => 'Every call',
                        _ => 'None',
                      }}',
                      style: const TextStyle(fontSize: 12),
                    ),
                  ],
                ),
              const SizedBox(height: 8),
              Text(
                'Next call: ${p.data['next_call_at'] == null ? 'Not scheduled' : utcTimestamp(p.data['next_call_at'])}',
                style: const TextStyle(fontSize: 12, color: Color(0xFF62766F)),
              ),
              Disclosure(
                title: const Text('Review AI interpretation'),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    if (p.context.isNotEmpty) ...[
                      SelectableText(p.context),
                      const SizedBox(height: 10),
                    ],
                    const Text(
                      'Tracking',
                      style: TextStyle(fontWeight: FontWeight.w600),
                    ),
                    for (final item in p.trackedItems)
                      Text(
                        '• ${item.label}${item.unit == null ? '' : ' (${item.unit})'}${item.options.isEmpty ? '' : ' · ${item.options.join(', ')}'}',
                      ),
                    const SizedBox(height: 10),
                    const Text(
                      'Questions',
                      style: TextStyle(fontWeight: FontWeight.w600),
                    ),
                    for (final question in p.strings('questions'))
                      Text('• $question'),
                    const SizedBox(height: 10),
                    const Text(
                      'Review this interpretation before approving a call. Edit the instructions to correct it.',
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
        Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: PlanResults(plan: p),
        ),
        _section('Photos & quality', PlanPhotos(plan: p, api: widget.api)),
        _section(
          'Call history',
          Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (p.calls.isEmpty)
                Text(
                  p.data['last_summary'] == null
                      ? 'No call results available.'
                      : 'Latest AI summary: ${p.data['last_summary']}',
                ),
              for (final call in p.calls)
                Disclosure(
                  title: Text(
                    '${utcTimestamp(call['started_at'] ?? call['created_at'])} · ${call['mock'] == true ? 'Simulated' : call['status'] ?? 'Status not provided'}',
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      SelectableText('AI summary: ${_value(call['summary'])}'),
                      if (call['transcript'] != null)
                        Disclosure(
                          title: const Text('Transcript'),
                          child: SelectableText('${call['transcript']}'),
                        ),
                      if (call['results'] != null)
                        SelectableText(
                          'AI interpretation: ${_value(call['results'])}',
                        ),
                      if (call['concerns'] != null)
                        SelectableText('Concerns: ${_value(call['concerns'])}'),
                      if (call['photos'] != null)
                        SelectableText('Photos: ${_value(call['photos'])}'),
                      const SizedBox(height: 10),
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
