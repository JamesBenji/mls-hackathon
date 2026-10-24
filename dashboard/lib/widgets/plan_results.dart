import 'package:flutter/material.dart';
import '../models/follow_up_plan.dart';
import 'workspace_widgets.dart';
import 'observation_chart.dart';

export 'observation_chart.dart' show NumericObservationChart;

class PlanResults extends StatelessWidget {
  final FollowUpPlan plan;
  const PlanResults({super.key, required this.plan});
  @override
  Widget build(BuildContext context) => SectionCard(
    title: 'Patient-reported tracking',
    subtitle: 'Expand an observation for its source quote and call context.',
    child: LayoutBuilder(
      builder: (context, constraints) {
        final width = constraints.maxWidth >= 560
            ? (constraints.maxWidth - 12) / 2
            : constraints.maxWidth;
        return Wrap(
          spacing: 12,
          runSpacing: 12,
          children: [
            if (plan.resultItems.isEmpty)
              const Text('No tracking items have been derived yet.'),
            for (final item in plan.resultItems)
              SizedBox(
                width: width,
                child: _TrackingResult(plan: plan, item: item),
              ),
          ],
        );
      },
    ),
  );
}

class _TrackingResult extends StatelessWidget {
  final FollowUpPlan plan;
  final TrackingItem item;
  const _TrackingResult({required this.plan, required this.item});
  @override
  Widget build(BuildContext context) {
    final series = observationSeries(plan, item);
    final observations =
        plan.observations.where((o) => o.itemId == item.id).toList()
          ..sort(compareObservationTime);
    final latest = observations.isEmpty ? null : observations.last;
    final currentRevision = plan.revision;
    final current =
        latest != null &&
        currentRevision != null &&
        latest.revision == currentRevision &&
        latest.unit == item.unit &&
        (latest.definition == null ||
            latest.definition!.signature == item.signature);
    final latestLabel = latest == null
        ? 'No observations yet'
        : currentRevision != null && !current
        ? 'No compatible observations for the current plan definition'
        : '${current ? 'Latest' : 'Latest available · revision ${latest.revision}'}: ${latest.valueLabel}${latest.status == 'reported' && latest.unit != null ? ' ${latest.unit}' : ''}';
    return DecoratedBox(
      decoration: BoxDecoration(
        color: Colors.white,
        border: Border.all(color: const Color(0xFFDCE8E3)),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  width: 4,
                  height: 32,
                  decoration: BoxDecoration(
                    color: trackingColor(item.id),
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        item.label,
                        style: const TextStyle(
                          fontSize: 14,
                          fontWeight: FontWeight.w600,
                          color: ink,
                        ),
                      ),
                      Text(
                        '${item.type}${item.unit == null ? '' : ' · ${item.unit}'}',
                        style: const TextStyle(
                          fontSize: 11,
                          color: Color(0xFF62766F),
                        ),
                      ),
                    ],
                  ),
                ),
                if (latest != null && current && latest.status == 'reported')
                  Flexible(
                    child: Text(
                      '${latest.valueLabel}${latest.unit == null ? '' : ' ${latest.unit}'}',
                      textAlign: TextAlign.end,
                      style: TextStyle(
                        fontSize: 22,
                        fontWeight: FontWeight.w700,
                        color: trackingColor(item.id),
                      ),
                    ),
                  ),
              ],
            ),
            const SizedBox(height: 12),
            Text(
              latestLabel,
              style: const TextStyle(fontSize: 12, color: Color(0xFF62766F)),
            ),
            if (latest != null && (current || currentRevision == null)) ...[
              Text(
                'Reported: ${latest.reportedAt}',
                style: const TextStyle(fontSize: 11, color: Color(0xFF62766F)),
              ),
              if (latest.data['measured_at'] != null)
                Text(
                  'Measured: ${latest.measuredAt}',
                  style: const TextStyle(
                    fontSize: 11,
                    color: Color(0xFF62766F),
                  ),
                ),
            ],
            if (item.type == 'photo')
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 8),
                child: Text(
                  'See Photos & quality below for uploaded images and technical feedback.',
                ),
              ),
            for (final group in series)
              Padding(
                padding: const EdgeInsets.only(top: 14),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text(
                      'REVISION ${group.revision}${group.unit == null ? '' : ' · ${group.unit}'}',
                      style: const TextStyle(
                        fontSize: 10,
                        fontWeight: FontWeight.w600,
                        letterSpacing: 0.6,
                        color: Color(0xFF62766F),
                      ),
                    ),
                    if (group.item.type == 'number' &&
                        group.revision != 'unknown')
                      NumericObservationChart(
                        series: group,
                        detailBuilder: (observation) => ObservationRow(
                          observation: observation,
                          initiallyExpanded: true,
                        ),
                      ),
                    Disclosure(
                      title: Text(
                        group.item.type == 'number'
                            ? 'Observation table & sources'
                            : 'Timeline & sources',
                      ),
                      child: Column(
                        children: [
                          for (final observation in group.observations.where(
                            group.compatible,
                          ))
                            ObservationRow(observation: observation),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            if (series.isEmpty)
              const Text('Waiting for the first reported answer.'),
            const SizedBox(height: 4),
          ],
        ),
      ),
    );
  }
}

class ObservationRow extends StatelessWidget {
  final PlanObservation observation;
  final bool initiallyExpanded;
  const ObservationRow({
    super.key,
    required this.observation,
    this.initiallyExpanded = false,
  });
  @override
  Widget build(BuildContext context) {
    final o = observation;
    return Disclosure(
      initiallyExpanded: initiallyExpanded,
      title: Text(
        '${o.definition?.label ?? o.itemId}: ${o.valueLabel}${o.status == 'reported' && o.unit != null ? ' ${o.unit}' : ''}',
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Reported: ${o.reportedAt}'),
          SelectableText('Patient source: “${o.quote}”'),
          Text('Measured: ${o.measuredAt}'),
          Text('Call: ${o.callId} · revision ${o.revision} · ${o.status}'),
        ],
      ),
    );
  }
}
