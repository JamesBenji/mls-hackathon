import 'package:flutter/material.dart';
import 'package:forui/forui.dart';
import '../models/patient.dart';
import '../services/dashboard_api.dart';
import '../widgets/clinical_widgets.dart';

class TriagePanel extends StatefulWidget {
  final DashboardApi api;
  final Future<List<Patient>> patients;
  const TriagePanel({super.key, required this.api, required this.patients});
  @override
  State<TriagePanel> createState() => _TriagePanelState();
}

class _TriagePanelState extends State<TriagePanel> {
  late Future<List<TriageDecision>> _future;
  final Set<int> _busy = {};
  final Map<int, String> _errors = {};
  final Map<int, String> _overrides = {};
  bool _loading = false;
  @override
  void initState() {
    super.initState();
    _load();
  }

  void _load() {
    if (_loading) return;
    _loading = true;
    _future = widget.api.triage().whenComplete(() {
      if (mounted) setState(() => _loading = false);
    });
  }

  void _refresh() => setState(_load);
  Future<void> _review(TriageDecision decision) async {
    if (_busy.contains(decision.id)) return;
    setState(() {
      _busy.add(decision.id);
      _errors.remove(decision.id);
    });
    try {
      await widget.api.review(
        decision.id,
        overrideAction: _overrides[decision.id],
      );
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Review recorded. No call or appointment was created.'),
        ),
      );
      _refresh();
    } catch (error) {
      if (mounted) setState(() => _errors[decision.id] = '$error');
    } finally {
      if (mounted) setState(() => _busy.remove(decision.id));
    }
  }

  @override
  Widget build(BuildContext context) => ListView(
    padding: const EdgeInsets.all(24),
    children: [
      const Text(
        'Triage review',
        style: TextStyle(fontSize: 30, fontWeight: FontWeight.w600),
      ),
      const SizedBox(height: 10),
      const Text(
        'Review clinical recommendations. Recording a review does not execute the recommended action.',
      ),
      const SizedBox(height: 18),
      Align(
        alignment: Alignment.centerLeft,
        child: ClinicalButton(
          variant: FButtonVariant.outline,
          mainAxisSize: MainAxisSize.min,
          onPress: _loading || _busy.isNotEmpty ? null : _refresh,
          child: const Text('Refresh triage'),
        ),
      ),
      const SizedBox(height: 24),
      AsyncContent(
        future: _future,
        retry: _refresh,
        builder: (decisions) => Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (decisions.isEmpty)
              const SectionCard(
                title: 'Review queue is clear',
                child: Text('No decisions are awaiting doctor review.'),
              ),
            for (final decision in decisions)
              Padding(
                padding: const EdgeInsets.only(bottom: 18),
                child: SectionCard(
                  title: 'Patient #${decision.patientId}',
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      FutureBuilder(
                        future: widget.patients,
                        builder: (context, snapshot) {
                          final matches = (snapshot.data ?? <Patient>[]).where(
                            (p) => p.id == decision.patientId,
                          );
                          return matches.isEmpty
                              ? const SizedBox.shrink()
                              : Padding(
                                  padding: const EdgeInsets.only(bottom: 12),
                                  child: Text(
                                    matches.first.name,
                                    style: const TextStyle(
                                      fontWeight: FontWeight.w600,
                                    ),
                                  ),
                                );
                        },
                      ),
                      RiskBadge(decision.urgency),
                      const SizedBox(height: 14),
                      Text(
                        'Recommended: ${decision.action.replaceAll('_', ' ')}',
                        style: const TextStyle(fontWeight: FontWeight.w600),
                      ),
                      const SizedBox(height: 10),
                      Text(decision.rationale),
                      const SizedBox(height: 16),
                      DropdownButtonFormField<String>(
                        initialValue: null,
                        isExpanded: true,
                        decoration: const InputDecoration(
                          labelText: 'Doctor decision',
                          border: OutlineInputBorder(),
                        ),
                        items: [
                          const DropdownMenuItem(
                            value: null,
                            child: Text('Confirm recommendation'),
                          ),
                          for (final action in [
                            'none',
                            'doctor_callback',
                            'ambulance',
                            'request_appointment',
                          ])
                            DropdownMenuItem(
                              value: action,
                              child: Text(action.replaceAll('_', ' ')),
                            ),
                        ],
                        onChanged: _busy.contains(decision.id)
                            ? null
                            : (value) => setState(() {
                                if (value == null) {
                                  _overrides.remove(decision.id);
                                } else {
                                  _overrides[decision.id] = value;
                                }
                              }),
                      ),
                      const SizedBox(height: 16),
                      if (_errors[decision.id] != null)
                        Padding(
                          padding: const EdgeInsets.only(bottom: 12),
                          child: Text(
                            _errors[decision.id]!,
                            style: const TextStyle(color: Colors.red),
                          ),
                        ),
                      ClinicalButton(
                        mainAxisSize: MainAxisSize.min,
                        onPress: _busy.contains(decision.id)
                            ? null
                            : () => _review(decision),
                        child: Text(
                          _busy.contains(decision.id)
                              ? 'Recording…'
                              : 'Record doctor review',
                        ),
                      ),
                    ],
                  ),
                ),
              ),
          ],
        ),
      ),
    ],
  );
}
