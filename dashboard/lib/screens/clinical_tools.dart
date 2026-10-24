import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:forui/forui.dart';
import '../models/patient.dart';
import '../services/dashboard_api.dart';
import '../widgets/clinical_widgets.dart';

class ClinicalTools extends StatefulWidget {
  final DashboardApi api;
  final int patientId;
  const ClinicalTools({super.key, required this.api, required this.patientId});
  @override
  State<ClinicalTools> createState() => _ClinicalToolsState();
}

class _ClinicalToolsState extends State<ClinicalTools> {
  final _transcript = TextEditingController();
  bool _busy = false;
  String? _error;
  List<String>? _questions;
  Json? _analysis;
  @override
  void dispose() {
    _transcript.dispose();
    super.dispose();
  }

  Future<void> _run({bool analyze = false}) async {
    if (_busy) return;
    if (analyze && _transcript.text.trim().isEmpty) {
      setState(() => _error = 'Enter a transcript before analyzing.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      if (analyze) {
        final result = await widget.api.analyze(
          widget.patientId,
          _transcript.text.trim(),
        );
        if (mounted) setState(() => _analysis = result);
      } else {
        final result = await widget.api.questionPlan(widget.patientId);
        if (mounted) setState(() => _questions = result);
      }
    } catch (error) {
      if (mounted) setState(() => _error = '$error');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => SectionCard(
    title: 'Clinical support',
    subtitle:
        'Runs only when requested. Generated guidance requires doctor review; no follow-up is initiated.',
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const Text(
          'Question plan',
          style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
        ),
        const SizedBox(height: 8),
        const Text(
          'Generate history-aware questions using the clinical service. This may take up to two minutes.',
        ),
        const SizedBox(height: 14),
        Align(
          alignment: Alignment.centerLeft,
          child: ClinicalButton(
            mainAxisSize: MainAxisSize.min,
            variant: FButtonVariant.outline,
            onPress: _busy ? null : () => _run(),
            child: Text(
              _questions == null
                  ? 'Generate question plan'
                  : 'Regenerate question plan',
            ),
          ),
        ),
        if (_questions != null) ...[
          const SizedBox(height: 16),
          if (_questions!.isEmpty) const Text('No questions returned.'),
          for (final (index, question) in _questions!.indexed)
            Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: SelectableText(
                '${index + 1}. $question',
                style: const TextStyle(height: 1.6),
              ),
            ),
        ],
        const Padding(
          padding: EdgeInsets.symmetric(vertical: 20),
          child: Divider(),
        ),
        const Text(
          'Transcript analysis',
          style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
        ),
        const SizedBox(height: 8),
        const Text(
          'Analyze a conversation without saving a report or creating a triage decision.',
        ),
        const SizedBox(height: 14),
        TextField(
          controller: _transcript,
          enabled: !_busy,
          minLines: 3,
          maxLines: 8,
          maxLength: 20000,
          decoration: const InputDecoration(
            labelText: 'Conversation transcript',
            border: OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        Align(
          alignment: Alignment.centerLeft,
          child: ClinicalButton(
            mainAxisSize: MainAxisSize.min,
            onPress: _busy ? null : () => _run(analyze: true),
            child: const Text('Analyze transcript'),
          ),
        ),
        if (_busy)
          const Padding(
            padding: EdgeInsets.only(top: 18),
            child: Row(
              children: [
                SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
                SizedBox(width: 12),
                Expanded(child: Text('Clinical service is working…')),
              ],
            ),
          ),
        if (_error != null)
          Padding(
            padding: const EdgeInsets.only(top: 16),
            child: Text(_error!, style: const TextStyle(color: Colors.red)),
          ),
        if (_analysis != null) ...[
          const SizedBox(height: 22),
          const Text(
            'Analysis result · Not saved',
            style: TextStyle(fontWeight: FontWeight.w600),
          ),
          for (final key in [
            'summary',
            'soap_note',
            'urgency',
            'recommended_action',
            'risk_flags',
            'structured_data',
          ])
            if (_analysis![key] != null)
              Padding(
                padding: const EdgeInsets.only(top: 14),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      key.replaceAll('_', ' ').toUpperCase(),
                      style: const TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(height: 6),
                    SelectableText(
                      _analysis![key] is String
                          ? _analysis![key] as String
                          : const JsonEncoder.withIndent(
                              '  ',
                            ).convert(_analysis![key]),
                      style: const TextStyle(height: 1.6),
                    ),
                  ],
                ),
              ),
        ],
      ],
    ),
  );
}
