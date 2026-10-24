import 'package:flutter/material.dart';
import '../models/follow_up_plan.dart';
import 'workspace_widgets.dart';

class PlanResults extends StatelessWidget {
  final FollowUpPlan plan;
  const PlanResults({super.key, required this.plan});
  @override
  Widget build(BuildContext context) => SectionCard(
    title: 'Patient-reported tracking',
    subtitle: 'Expand an observation for its source quote and call context.',
    child: LayoutBuilder(
      builder: (context, constraints) {
        final width = constraints.maxWidth >= 640
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
        latest.revision == currentRevision;
    final latestLabel = latest == null
        ? 'No observations yet'
        : currentRevision != null && !current
        ? 'No observations for the current plan revision'
        : '${current ? 'Latest' : 'Latest available · revision ${latest.revision}'}: ${latest.valueLabel}${latest.status == 'reported' && latest.unit != null ? ' ${latest.unit}' : ''}';
    return DecoratedBox(
      decoration: BoxDecoration(
        color: const Color(0xFFF5F8F6),
        border: Border.all(color: const Color(0xFFDCE8E3)),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              item.label,
              style: const TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 8),
            Text(
              latestLabel,
              style: const TextStyle(fontWeight: FontWeight.w600),
            ),
            if (latest != null && (current || currentRevision == null)) ...[
              Text('Reported: ${latest.reportedAt}'),
              Text('Measured: ${latest.measuredAt}'),
            ],
            if (item.type == 'photo')
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 8),
                child: Text(
                  'Attachment metadata only. Image content / authorized preview is not available from this API.',
                ),
              ),
            for (final group in series)
              Padding(
                padding: const EdgeInsets.only(top: 14),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text(
                      'Revision ${group.revision} · ${group.unit ?? 'no unit'}',
                      style: const TextStyle(fontWeight: FontWeight.w500),
                    ),
                    if (group.item.type == 'number' &&
                        group.revision != 'unknown')
                      NumericObservationChart(series: group),
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

class NumericObservationChart extends StatefulWidget {
  final ObservationSeries series;
  const NumericObservationChart({super.key, required this.series});
  @override
  State<NumericObservationChart> createState() =>
      _NumericObservationChartState();
}

class _NumericObservationChartState extends State<NumericObservationChart> {
  int? _selected;
  @override
  Widget build(BuildContext context) {
    final series = widget.series;
    final points = series.numericPoints;
    final values = points.whereType<num>().toList();
    if (values.isEmpty) {
      return const Padding(
        padding: EdgeInsets.all(12),
        child: Text('No reported numeric values in this series.'),
      );
    }
    final min = values.reduce((a, b) => a < b ? a : b);
    final max = values.reduce((a, b) => a > b ? a : b);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const SizedBox(height: 12),
        Text(
          'Observed range: $min–$max ${series.unit ?? ''} · not a target range',
        ),
        Semantics(
          label:
              '${series.item.label} trend. ${points.length} observations. Missing data are gaps. A textual observation table is below.',
          child: LayoutBuilder(
            builder: (context, constraints) => GestureDetector(
              onTapDown: (event) {
                final usable = constraints.maxWidth - 32;
                final fraction = usable <= 0
                    ? 0.0
                    : (event.localPosition.dx - 16) / usable;
                setState(
                  () => _selected = (fraction * (points.length - 1))
                      .round()
                      .clamp(0, points.length - 1),
                );
              },
              child: SizedBox(
                height: 155,
                width: double.infinity,
                child: CustomPaint(painter: _TrendPainter(points)),
              ),
            ),
          ),
        ),
        Row(
          children: [
            Expanded(
              child: Text(
                series.observations.first.reportedAt,
                style: const TextStyle(fontSize: 11),
              ),
            ),
            Expanded(
              child: Text(
                series.observations.last.reportedAt,
                textAlign: TextAlign.end,
                style: const TextStyle(fontSize: 11),
              ),
            ),
          ],
        ),
        const Text(
          'Each position is a report, ordered by reported time; spacing is not elapsed time. Tap a position or use the table to inspect it.',
          style: TextStyle(fontSize: 12),
        ),
        if (_selected != null)
          ObservationRow(
            key: ValueKey(_selected),
            observation: series.observations[_selected!],
            initiallyExpanded: true,
          ),
      ],
    );
  }
}

class _TrendPainter extends CustomPainter {
  final List<num?> points;
  _TrendPainter(this.points);
  @override
  void paint(Canvas canvas, Size size) {
    final values = points.whereType<num>().toList();
    if (values.isEmpty) return;
    final min = values.reduce((a, b) => a < b ? a : b).toDouble();
    final max = values.reduce((a, b) => a > b ? a : b).toDouble();
    final range = max == min ? 1.0 : max - min;
    final paint = Paint()
      ..color = teal
      ..strokeWidth = 2;
    canvas.drawLine(
      Offset(16, size.height - 16),
      Offset(size.width - 16, size.height - 16),
      Paint()..color = const Color(0xFFDCE8E3),
    );
    Offset? previous;
    for (var i = 0; i < points.length; i++) {
      final value = points[i];
      if (value == null) {
        previous = null;
        continue;
      }
      final point = Offset(
        points.length == 1
            ? size.width / 2
            : 16 + i * (size.width - 32) / (points.length - 1),
        max == min
            ? size.height / 2
            : size.height -
                  20 -
                  (value.toDouble() - min) / range * (size.height - 40),
      );
      if (previous != null) canvas.drawLine(previous, point, paint);
      canvas.drawCircle(point, 4, paint);
      previous = point;
    }
  }

  @override
  bool shouldRepaint(_TrendPainter oldDelegate) => true;
}
