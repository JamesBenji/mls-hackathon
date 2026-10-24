import 'package:flutter/material.dart';

import '../models/follow_up_plan.dart';

const _muted = Color(0xFF62766F);
const _plotLeft = 44.0;
const _plotRight = 12.0;

/// Stable item colors identify series, never clinical severity.
Color trackingColor(String itemId) {
  const colors = [
    Color(0xFF16766D),
    Color(0xFF387DB2),
    Color(0xFF8263A8),
    Color(0xFFB37937),
  ];
  final index = itemId.codeUnits.fold(0, (total, value) => total + value);
  return colors[index % colors.length];
}

String _number(num value) {
  if (value == value.roundToDouble()) return value.toInt().toString();
  return value.toString();
}

String _date(DateTime date) {
  const months = [
    'Jan',
    'Feb',
    'Mar',
    'Apr',
    'May',
    'Jun',
    'Jul',
    'Aug',
    'Sep',
    'Oct',
    'Nov',
    'Dec',
  ];
  return '${date.day} ${months[date.month - 1]} · ${date.hour.toString().padLeft(2, '0')}:${date.minute.toString().padLeft(2, '0')}';
}

class NumericObservationChart extends StatefulWidget {
  const NumericObservationChart({
    super.key,
    required this.series,
    required this.detailBuilder,
  });
  final ObservationSeries series;
  final Widget Function(PlanObservation) detailBuilder;

  @override
  State<NumericObservationChart> createState() =>
      _NumericObservationChartState();
}

class _NumericObservationChartState extends State<NumericObservationChart> {
  String? _selectedId;

  @override
  Widget build(BuildContext context) {
    final data = _ChartData(widget.series);
    if (data.values.isEmpty || data.firstTime == null) {
      return const Padding(
        padding: EdgeInsets.symmetric(vertical: 16),
        child: Text(
          'Waiting for numeric readings with valid report timestamps.',
          style: TextStyle(fontSize: 12, color: _muted),
        ),
      );
    }
    final color = trackingColor(widget.series.item.id);
    final selected = _selectedId == null
        ? -1
        : data.observations.indexWhere(
            (o) => o.data['id']?.toString() == _selectedId,
          );
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const SizedBox(height: 10),
        Text(
          'Observed ${_number(data.minimum)}–${_number(data.maximum)} ${widget.series.unit ?? ''}',
          style: const TextStyle(fontSize: 11, color: _muted),
        ),
        const SizedBox(height: 4),
        Semantics(
          label:
              '${widget.series.item.label} trend by reported time. ${data.values.length} numeric readings. Missing answers break the line. Observation table and sources follow.',
          child: LayoutBuilder(
            builder: (context, constraints) => GestureDetector(
              onTapDown: (event) {
                final width = constraints.maxWidth - _plotLeft - _plotRight;
                if (width <= 0) return;
                final fraction = (event.localPosition.dx - _plotLeft) / width;
                int? closest;
                double distance = double.infinity;
                for (var index = 0; index < data.positions.length; index++) {
                  final position = data.positions[index];
                  if (position == null) continue;
                  final candidate = (position - fraction).abs();
                  if (candidate < distance) {
                    distance = candidate;
                    closest = index;
                  }
                }
                if (closest != null) {
                  setState(
                    () => _selectedId = data.observations[closest!].data['id']
                        ?.toString(),
                  );
                }
              },
              child: SizedBox(
                height: 170,
                width: double.infinity,
                child: CustomPaint(
                  painter: _TrendPainter(
                    data,
                    color,
                    selected,
                    Directionality.of(context),
                    DefaultTextStyle.of(context).style,
                  ),
                ),
              ),
            ),
          ),
        ),
        Padding(
          padding: const EdgeInsets.only(left: _plotLeft, right: _plotRight),
          child: Row(
            children: [
              Expanded(
                child: Text(
                  _date(data.firstTime!),
                  style: const TextStyle(fontSize: 10, color: _muted),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  _date(data.lastTime!),
                  textAlign: TextAlign.end,
                  style: const TextStyle(fontSize: 10, color: _muted),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),
        Wrap(
          spacing: 14,
          runSpacing: 6,
          children: [
            _Legend(color: color, text: 'Patient reported'),
            if (data.points.any((value) => value == null))
              const _Legend(color: _muted, text: 'No reading', hollow: true),
            const Text(
              'Reported time · UTC',
              style: TextStyle(fontSize: 10, color: _muted),
            ),
          ],
        ),
        const SizedBox(height: 6),
        const Text(
          'Observed range, not a clinical target. Select a point for its source.',
          style: TextStyle(fontSize: 10, color: _muted),
        ),
        if (data.positions.any((position) => position == null))
          const Text(
            'Undated observations remain in the table, not on the chart.',
            style: TextStyle(fontSize: 10, color: _muted),
          ),
        if (selected >= 0)
          KeyedSubtree(
            key: ValueKey(_selectedId),
            child: widget.detailBuilder(data.observations[selected]),
          ),
      ],
    );
  }
}

class _Legend extends StatelessWidget {
  const _Legend({required this.color, required this.text, this.hollow = false});
  final Color color;
  final String text;
  final bool hollow;

  @override
  Widget build(BuildContext context) => Row(
    mainAxisSize: MainAxisSize.min,
    children: [
      Container(
        width: 7,
        height: 7,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: hollow ? Colors.white : color,
          border: Border.all(color: color),
        ),
      ),
      const SizedBox(width: 5),
      Text(text, style: const TextStyle(fontSize: 10, color: _muted)),
    ],
  );
}

class _ChartData {
  _ChartData(ObservationSeries series) : observations = series.observations {
    points = series.numericPoints
        .map(
          (value) => value != null && value.isFinite ? value.toDouble() : null,
        )
        .toList();
    final times = observations
        .map((o) => parseUtcTimestamp(o.data['reported_at']))
        .toList();
    final validTimes = times.whereType<DateTime>().toList()..sort();
    firstTime = validTimes.isEmpty ? null : validTimes.first;
    lastTime = validTimes.isEmpty ? null : validTimes.last;
    final duration = firstTime == null
        ? 0
        : lastTime!.difference(firstTime!).inMicroseconds;
    positions = times.map((time) {
      if (time == null) return null;
      return duration == 0
          ? 0.5
          : time.difference(firstTime!).inMicroseconds / duration;
    }).toList();
    values = [
      for (var index = 0; index < points.length; index++)
        if (points[index] != null && positions[index] != null) points[index]!,
    ];
    minimum = values.isEmpty ? 0 : values.reduce((a, b) => a < b ? a : b);
    maximum = values.isEmpty ? 0 : values.reduce((a, b) => a > b ? a : b);
  }

  final List<PlanObservation> observations;
  late final List<double?> points, positions;
  late final List<double> values;
  late final double minimum, maximum;
  late final DateTime? firstTime, lastTime;
}

class _TrendPainter extends CustomPainter {
  _TrendPainter(
    this.data,
    this.color,
    this.selected,
    this.textDirection,
    this.textStyle,
  );
  final _ChartData data;
  final Color color;
  final int selected;
  final TextDirection textDirection;
  final TextStyle textStyle;

  @override
  void paint(Canvas canvas, Size size) {
    final plot = Rect.fromLTRB(
      _plotLeft,
      14,
      size.width - _plotRight,
      size.height - 16,
    );
    if (plot.width <= 0) return;
    final padding = data.minimum == data.maximum
        ? (data.maximum.abs() * 0.1).clamp(1.0, double.infinity)
        : 0.0;
    final low = data.minimum - padding;
    final high = data.maximum + padding;
    final range = high - low;
    final grid = Paint()..color = const Color(0xFFE8EEEB);
    for (var index = 0; index < 3; index++) {
      final fraction = index / 2;
      final y = plot.bottom - fraction * plot.height;
      canvas.drawLine(Offset(plot.left, y), Offset(plot.right, y), grid);
      final label = TextPainter(
        text: TextSpan(
          text: _number(low + fraction * range),
          style: textStyle.copyWith(fontSize: 10, color: _muted),
        ),
        textDirection: textDirection,
        maxLines: 1,
      )..layout(maxWidth: _plotLeft - 8);
      label.paint(
        canvas,
        Offset(plot.left - label.width - 8, y - label.height / 2),
      );
    }
    final stroke = Paint()
      ..color = color
      ..strokeWidth = 2.2
      ..strokeCap = StrokeCap.round;
    Offset? previous;
    for (var index = 0; index < data.points.length; index++) {
      final fraction = data.positions[index];
      final value = data.points[index];
      if (fraction == null) {
        previous = null;
        continue;
      }
      final x = plot.left + fraction * plot.width;
      if (value == null) {
        previous = null;
        canvas.drawCircle(
          Offset(x, plot.bottom),
          3.5,
          Paint()
            ..color = _muted
            ..style = PaintingStyle.stroke
            ..strokeWidth = 1.3,
        );
        continue;
      }
      final point = Offset(
        x,
        plot.bottom - (value - low) / range * plot.height,
      );
      if (previous != null) canvas.drawLine(previous, point, stroke);
      if (index == selected) {
        canvas.drawCircle(
          point,
          8,
          Paint()..color = color.withValues(alpha: 0.15),
        );
      }
      canvas.drawCircle(point, 4, Paint()..color = Colors.white);
      canvas.drawCircle(point, 3, Paint()..color = color);
      previous = point;
    }
  }

  @override
  bool shouldRepaint(_TrendPainter oldDelegate) =>
      oldDelegate.data != data ||
      oldDelegate.selected != selected ||
      oldDelegate.color != color ||
      oldDelegate.textDirection != textDirection ||
      oldDelegate.textStyle != textStyle;
}
