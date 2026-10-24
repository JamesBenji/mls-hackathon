import 'package:flutter/material.dart';
import 'package:forui/forui.dart';
import '../app.dart';

/// Forui's default button row does not constrain its text slot. Keep labels
/// readable at narrow widths and larger accessibility text sizes.
class ClinicalButton extends FButton {
  ClinicalButton({
    super.key,
    required super.onPress,
    required Widget child,
    super.variant,
    super.size,
    super.mainAxisSize,
    super.mainAxisAlignment,
    super.prefix,
  }) : super(child: Flexible(child: child));
}

class SectionCard extends StatelessWidget {
  final String title;
  final String? subtitle;
  final Widget child;
  const SectionCard({
    super.key,
    required this.title,
    this.subtitle,
    required this.child,
  });
  @override
  Widget build(BuildContext context) => FCard(
    child: Padding(
      padding: const EdgeInsets.all(22),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            title,
            style: const TextStyle(
              fontSize: 18,
              fontWeight: FontWeight.w600,
              color: ink,
            ),
          ),
          if (subtitle != null)
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Text(
                subtitle!,
                style: const TextStyle(color: Color(0xFF62766F), height: 1.5),
              ),
            ),
          const SizedBox(height: 18),
          child,
        ],
      ),
    ),
  );
}

class RiskBadge extends StatelessWidget {
  final String risk;
  const RiskBadge(this.risk, {super.key});
  @override
  Widget build(BuildContext context) {
    final color = switch (risk) {
      'high' || 'emergency' || 'urgent' => const Color(0xFFAC3E3E),
      'medium' || 'monitor' => const Color(0xFF926613),
      'low' || 'none' => teal,
      _ => const Color(0xFF62766F),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: color.withValues(alpha: .09),
        borderRadius: BorderRadius.circular(30),
      ),
      child: Text(
        risk.replaceAll('_', ' ').toUpperCase(),
        style: TextStyle(
          color: color,
          fontSize: 11,
          fontWeight: FontWeight.w700,
          letterSpacing: .6,
        ),
      ),
    );
  }
}

class AsyncContent<T> extends StatelessWidget {
  final Future<T> future;
  final VoidCallback retry;
  final Widget Function(T) builder;
  const AsyncContent({
    super.key,
    required this.future,
    required this.retry,
    required this.builder,
  });
  @override
  Widget build(BuildContext context) => FutureBuilder<T>(
    future: future,
    builder: (context, snapshot) {
      if (snapshot.connectionState != ConnectionState.done) {
        return const Padding(
          padding: EdgeInsets.all(40),
          child: Center(
            child: CircularProgressIndicator(
              semanticsLabel: 'Loading clinical records',
            ),
          ),
        );
      }
      if (snapshot.hasError) {
        return SectionCard(
          title: 'Could not load records',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text('${snapshot.error}'),
              const SizedBox(height: 16),
              ClinicalButton(
                onPress: retry,
                mainAxisSize: MainAxisSize.min,
                child: const Text('Retry'),
              ),
            ],
          ),
        );
      }
      return builder(snapshot.data as T);
    },
  );
}

String display(dynamic value) =>
    value == null || '$value'.isEmpty ? 'Not recorded' : '$value';
String timestamp(dynamic value) {
  final date = DateTime.tryParse('$value');
  if (date == null) return 'Time not recorded';
  // The existing backend returns naive database timestamps; do not invent a zone.
  return '${date.year}-${date.month.toString().padLeft(2, '0')}-${date.day.toString().padLeft(2, '0')} · ${date.hour.toString().padLeft(2, '0')}:${date.minute.toString().padLeft(2, '0')}';
}
