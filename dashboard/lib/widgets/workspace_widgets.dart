import 'package:flutter/material.dart';
import 'package:forui/forui.dart';

const teal = Color(0xFF16766D);
const mint = Color(0xFFE5F4EE);
const ink = Color(0xFF183D38);

/// Forui defaults to a full-width button. Toolbar/action buttons must own their
/// intrinsic width, including when they are children of a Wrap or a loose Row.
class WorkspaceButton extends StatelessWidget {
  final VoidCallback? onPress;
  final Widget child;
  final FButtonVariant variant;
  final Widget? prefix;
  const WorkspaceButton({
    super.key,
    required this.onPress,
    required this.child,
    this.variant = FButtonVariant.primary,
    this.prefix,
  });
  @override
  Widget build(BuildContext context) => IntrinsicWidth(
    child: FButton(
      onPress: onPress,
      variant: variant,
      size: FButtonSizeVariant.sm,
      mainAxisSize: MainAxisSize.min,
      prefix: prefix,
      child: child,
    ),
  );
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
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(
            title,
            style: const TextStyle(
              fontSize: 16,
              fontWeight: FontWeight.w600,
              color: ink,
            ),
          ),
          if (subtitle != null) ...[
            const SizedBox(height: 4),
            Text(
              subtitle!,
              style: const TextStyle(fontSize: 12, color: Color(0xFF62766F)),
            ),
          ],
          const SizedBox(height: 10),
          child,
        ],
      ),
    ),
  );
}

class Disclosure extends StatelessWidget {
  final Widget title, child;
  final bool initiallyExpanded;
  const Disclosure({
    super.key,
    required this.title,
    required this.child,
    this.initiallyExpanded = false,
  });
  @override
  Widget build(BuildContext context) => FAccordion(
    children: [
      FAccordionItem(
        title: title,
        initiallyExpanded: initiallyExpanded,
        child: child,
      ),
    ],
  );
}

Future<bool?> confirmPlanAction(
  BuildContext context, {
  required String title,
  required String body,
  required String confirmLabel,
}) => showFDialog<bool>(
  context: context,
  builder: (context, style, animation) => FDialog(
    animation: animation,
    builder: (context, style) => Padding(
      padding: const EdgeInsets.all(20),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text(title, style: style.titleTextStyle),
          const SizedBox(height: 12),
          Flexible(
            child: SingleChildScrollView(
              child: Text(body, style: style.bodyTextStyle),
            ),
          ),
          const SizedBox(height: 20),
          Wrap(
            alignment: WrapAlignment.end,
            spacing: 8,
            runSpacing: 8,
            children: [
              WorkspaceButton(
                onPress: () => Navigator.pop(context, false),
                variant: FButtonVariant.outline,
                child: const Text('Cancel'),
              ),
              WorkspaceButton(
                onPress: () => Navigator.pop(context, true),
                child: Text(confirmLabel),
              ),
            ],
          ),
        ],
      ),
    ),
  ),
);
