import 'package:flutter/material.dart';
import '../app.dart';
import '../models/patient.dart';
import 'clinical_widgets.dart';

/// Renders trend line charts for patient vitals, grouped by type.
class VitalTrendsSection extends StatelessWidget {
  final List<Json> vitals;
  const VitalTrendsSection({super.key, required this.vitals});

  static String _formatType(String type) =>
      type.split('_').map((w) => '${w[0].toUpperCase()}${w.substring(1)}').join(' ');

  static Color _colorFor(String type) => switch (type) {
    'bp_systolic' || 'bp_diastolic' => const Color(0xFFE74C3C),
    'blood_sugar' => const Color(0xFFE67E22),
    'weight' => teal,
    'sleep_hours' => const Color(0xFF2980B9),
    _ => const Color(0xFF7F8C8D),
  };

  @override
  Widget build(BuildContext context) {
    // Group vitals by type.
    final grouped = <String, List<Json>>{};
    for (final v in vitals) {
      final type = v['type'] as String? ?? 'unknown';
      (grouped[type] ??= []).add(v);
    }

    // Sort each group by recorded_at ascending.
    for (final list in grouped.values) {
      list.sort((a, b) =>
          (a['recorded_at'] as String).compareTo(b['recorded_at'] as String));
    }

    // Combine BP systolic + diastolic into a single chart when both exist.
    final hasSystolic = (grouped['bp_systolic']?.length ?? 0) >= 2;
    final hasDiastolic = (grouped['bp_diastolic']?.length ?? 0) >= 2;
    final combinedBP = hasSystolic && hasDiastolic;

    // Build chart widgets.
    final charts = <Widget>[];

    if (combinedBP) {
      charts.add(_BPCombinedChart(
        systolic: grouped['bp_systolic']!,
        diastolic: grouped['bp_diastolic']!,
      ));
    }

    for (final entry in grouped.entries) {
      if (combinedBP &&
          (entry.key == 'bp_systolic' || entry.key == 'bp_diastolic')) {
        continue;
      }
      if (entry.value.length < 2) continue;
      charts.add(_VitalChart(
        title: _formatType(entry.key),
        data: entry.value,
        color: _colorFor(entry.key),
      ));
    }

    if (charts.isEmpty) {
      return const SizedBox.shrink();
    }

    return SectionCard(
      title: 'Vital Trends',
      child: LayoutBuilder(
        builder: (context, constraints) {
          final wide = constraints.maxWidth >= 560;
          final columnWidth = wide
              ? (constraints.maxWidth - 12) / 2
              : constraints.maxWidth;
          return Wrap(
            spacing: 12,
            runSpacing: 12,
            children: [
              for (final chart in charts)
                SizedBox(width: columnWidth, child: chart),
            ],
          );
        },
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Single-vital chart
// ---------------------------------------------------------------------------

class _VitalChart extends StatelessWidget {
  final String title;
  final List<Json> data;
  final Color color;
  const _VitalChart({
    required this.title,
    required this.data,
    required this.color,
  });

  @override
  Widget build(BuildContext context) {
    final values = data.map((d) => (d['value'] as num).toDouble()).toList();
    final unit = data.first['unit'] as String? ?? '';
    final latest = values.last;
    final min = values.reduce((a, b) => a < b ? a : b);
    final max = values.reduce((a, b) => a > b ? a : b);

    return DecoratedBox(
      decoration: BoxDecoration(
        color: const Color(0xFFF5F8F6),
        border: Border.all(color: const Color(0xFFDCE8E3)),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Header row: title + latest value.
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Flexible(
                  child: Text(
                    title,
                    style: const TextStyle(
                      fontSize: 13,
                      fontWeight: FontWeight.w600,
                      color: ink,
                    ),
                  ),
                ),
                Text(
                  '${latest % 1 == 0 ? latest.toInt() : latest} $unit',
                  style: TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.w700,
                    color: color,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 4),
            // Y-axis range.
            Text(
              '${min % 1 == 0 ? min.toInt() : min}–${max % 1 == 0 ? max.toInt() : max} $unit',
              style: const TextStyle(fontSize: 11, color: Color(0xFF62766F)),
            ),
            const SizedBox(height: 6),
            // Chart.
            SizedBox(
              height: 140,
              width: double.infinity,
              child: CustomPaint(
                painter: _VitalTrendPainter(values: values, color: color),
              ),
            ),
            // X-axis date labels.
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(
                  _shortDate(data.first['recorded_at'] as String),
                  style: const TextStyle(fontSize: 11, color: Color(0xFF62766F)),
                ),
                Text(
                  _shortDate(data.last['recorded_at'] as String),
                  style: const TextStyle(fontSize: 11, color: Color(0xFF62766F)),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Combined Blood Pressure chart (systolic + diastolic)
// ---------------------------------------------------------------------------

class _BPCombinedChart extends StatelessWidget {
  final List<Json> systolic;
  final List<Json> diastolic;
  const _BPCombinedChart({required this.systolic, required this.diastolic});

  @override
  Widget build(BuildContext context) {
    final sysValues =
        systolic.map((d) => (d['value'] as num).toDouble()).toList();
    final diaValues =
        diastolic.map((d) => (d['value'] as num).toDouble()).toList();
    final unit = systolic.first['unit'] as String? ?? 'mmHg';
    final latestSys = sysValues.last;
    final latestDia = diaValues.last;

    final allValues = [...sysValues, ...diaValues];
    final min = allValues.reduce((a, b) => a < b ? a : b);
    final max = allValues.reduce((a, b) => a > b ? a : b);

    return DecoratedBox(
      decoration: BoxDecoration(
        color: const Color(0xFFF5F8F6),
        border: Border.all(color: const Color(0xFFDCE8E3)),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                const Flexible(
                  child: Text(
                    'Blood Pressure',
                    style: TextStyle(
                      fontSize: 13,
                      fontWeight: FontWeight.w600,
                      color: ink,
                    ),
                  ),
                ),
                Text(
                  '${latestSys.toInt()}/${latestDia.toInt()} $unit',
                  style: const TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.w700,
                    color: Color(0xFFE74C3C),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 4),
            Text(
              '${min.toInt()}–${max.toInt()} $unit',
              style: const TextStyle(fontSize: 11, color: Color(0xFF62766F)),
            ),
            const SizedBox(height: 6),
            SizedBox(
              height: 140,
              width: double.infinity,
              child: CustomPaint(
                painter: _VitalTrendPainter(
                  values: sysValues,
                  color: const Color(0xFFE74C3C),
                  secondaryValues: diaValues,
                  secondaryColor: const Color(0xFFC0392B),
                  globalMin: min,
                  globalMax: max,
                ),
              ),
            ),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(
                  _shortDate(systolic.first['recorded_at'] as String),
                  style: const TextStyle(fontSize: 11, color: Color(0xFF62766F)),
                ),
                Text(
                  _shortDate(systolic.last['recorded_at'] as String),
                  style: const TextStyle(fontSize: 11, color: Color(0xFF62766F)),
                ),
              ],
            ),
            const SizedBox(height: 4),
            const Row(
              children: [
                _LegendDot(color: Color(0xFFE74C3C), label: 'Systolic'),
                SizedBox(width: 12),
                _LegendDot(color: Color(0xFFC0392B), label: 'Diastolic'),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Legend dot
// ---------------------------------------------------------------------------

class _LegendDot extends StatelessWidget {
  final Color color;
  final String label;
  const _LegendDot({required this.color, required this.label});

  @override
  Widget build(BuildContext context) => Row(
    mainAxisSize: MainAxisSize.min,
    children: [
      Container(
        width: 8,
        height: 8,
        decoration: BoxDecoration(color: color, shape: BoxShape.circle),
      ),
      const SizedBox(width: 4),
      Text(label, style: const TextStyle(fontSize: 11, color: Color(0xFF62766F))),
    ],
  );
}

// ---------------------------------------------------------------------------
// CustomPainter for trend lines
// ---------------------------------------------------------------------------

class _VitalTrendPainter extends CustomPainter {
  final List<double> values;
  final Color color;
  final List<double>? secondaryValues;
  final Color? secondaryColor;
  final double? globalMin;
  final double? globalMax;

  _VitalTrendPainter({
    required this.values,
    required this.color,
    this.secondaryValues,
    this.secondaryColor,
    this.globalMin,
    this.globalMax,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final allVals = [...values, ...?secondaryValues];
    final min = globalMin ?? allVals.reduce((a, b) => a < b ? a : b);
    final max = globalMax ?? allVals.reduce((a, b) => a > b ? a : b);
    final range = max == min ? 1.0 : max - min;

    // Baseline.
    canvas.drawLine(
      Offset(16, size.height - 16),
      Offset(size.width - 16, size.height - 16),
      Paint()..color = const Color(0xFFDCE8E3),
    );

    _drawLine(canvas, size, values, color, min, range);
    if (secondaryValues != null && secondaryColor != null) {
      _drawLine(canvas, size, secondaryValues!, secondaryColor!, min, range);
    }
  }

  void _drawLine(
    Canvas canvas,
    Size size,
    List<double> pts,
    Color c,
    double min,
    double range,
  ) {
    final paint = Paint()
      ..color = c
      ..strokeWidth = 2;

    Offset? previous;
    for (var i = 0; i < pts.length; i++) {
      final x = pts.length == 1
          ? size.width / 2
          : 16 + i * (size.width - 32) / (pts.length - 1);
      final y = size.height -
          20 -
          (pts[i] - min) / range * (size.height - 40);
      final point = Offset(x, y);
      if (previous != null) canvas.drawLine(previous, point, paint);
      canvas.drawCircle(point, 4, paint);
      previous = point;
    }
  }

  @override
  bool shouldRepaint(_VitalTrendPainter oldDelegate) => true;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

String _shortDate(String iso) {
  final date = DateTime.tryParse(iso);
  if (date == null) return iso;
  return '${date.month}/${date.day}';
}
