import 'package:flutter/material.dart';
import 'package:forui/forui.dart';

abstract final class CareColors {
  static const background = Color(0xFFF7F9F8);
  static const ink = Color(0xFF173C37);
  static const teal = Color(0xFF187D6D);
  static const mint = Color(0xFFE9F4EF);
  static const muted = Color(0xFF71817C);
  static const border = Color(0xFFE1E9E5);
  static const amber = Color(0xFF956217);
}

class CarePage extends StatelessWidget {
  const CarePage({required this.child, this.bottom, super.key});

  final Widget child;
  final Widget? bottom;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: CareColors.background,
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 600),
            child: Column(
              children: [
                Expanded(child: child),
                ?bottom,
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class CarePanel extends StatelessWidget {
  const CarePanel({
    required this.child,
    this.color = Colors.white,
    this.padding = const EdgeInsets.all(22),
    super.key,
  });

  final Widget child;
  final Color color;
  final EdgeInsetsGeometry padding;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: padding,
      decoration: BoxDecoration(
        color: color,
        borderRadius: BorderRadius.circular(24),
        border: Border.all(color: CareColors.border),
      ),
      child: child,
    );
  }
}

class StatusPill extends StatelessWidget {
  const StatusPill({required this.label, this.pending = false, super.key});

  final String label;
  final bool pending;

  @override
  Widget build(BuildContext context) {
    final color = pending ? CareColors.amber : CareColors.teal;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: pending ? const Color(0xFFFFF4DE) : CareColors.mint,
        borderRadius: BorderRadius.circular(30),
      ),
      child: Text(
        label,
        style: TextStyle(
          color: color,
          fontSize: 11,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}

class IconTile extends StatelessWidget {
  const IconTile({required this.icon, this.size = 46, super.key});

  final IconData icon;
  final double size;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        color: CareColors.mint,
        borderRadius: BorderRadius.circular(15),
      ),
      child: Icon(icon, color: CareColors.teal, size: size * 0.46),
    );
  }
}

class SectionHeading extends StatelessWidget {
  const SectionHeading({required this.title, this.trailing, super.key});

  final String title;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        Expanded(
          child: Text(
            title,
            style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600),
          ),
        ),
        ?trailing,
      ],
    );
  }
}

class PageBackHeader extends StatelessWidget {
  const PageBackHeader({required this.title, super.key});

  final String title;

  @override
  Widget build(BuildContext context) {
    return Row(
      children: [
        FButton.icon(
          variant: FButtonVariant.ghost,
          semanticsLabel: 'Go back',
          onPress: () => Navigator.of(context).pop(),
          child: const Icon(Icons.arrow_back_rounded),
        ),
        const SizedBox(width: 8),
        Text(title, style: const TextStyle(fontWeight: FontWeight.w600)),
        const Spacer(),
        const StatusPill(label: 'DEMO'),
      ],
    );
  }
}

const bodyStyle = TextStyle(fontSize: 14, height: 1.6, color: CareColors.muted);
