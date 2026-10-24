import 'package:flutter/material.dart';
import '../models/follow_up_plan.dart';
import '../services/plan_api.dart';
import '../widgets/clinical_widgets.dart';

class PlanEditor extends StatefulWidget {
  final PlanApi api;
  final int doctorId;
  final FollowUpPlan? plan;
  final bool sample;
  const PlanEditor({
    super.key,
    required this.api,
    required this.doctorId,
    this.plan,
    this.sample = false,
  });
  @override
  State<PlanEditor> createState() => _PlanEditorState();
}

class _PlanEditorState extends State<PlanEditor> {
  final _form = GlobalKey<FormState>();
  final Map<String, TextEditingController> _fields = {};
  List<PlanPatient> _patients = [];
  int? _patientId;
  bool _loading = false, _busy = false;
  String? _error;
  String _frequency = 'once';
  bool get _creating => widget.plan == null;
  @override
  void initState() {
    super.initState();
    final p = widget.plan;
    final initial = <String, String>{
      'instructions': p?.instructions ?? '',
      for (final key in ['timezone', 'local_time', 'start_date', 'end_date'])
        key: '${p?.schedule[key] ?? ''}',
    };
    for (final entry in initial.entries) {
      _fields[entry.key] = TextEditingController(text: entry.value);
    }
    _frequency = '${p?.schedule['frequency'] ?? 'once'}';
    if (_creating) _loadPatients();
  }

  Future<void> _loadPatients() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final patients = await widget.api.patients();
      if (mounted) {
        setState(() {
          _patients = patients;
          _loading = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = '$e';
          _loading = false;
        });
      }
    }
  }

  @override
  void dispose() {
    for (final c in _fields.values) {
      c.dispose();
    }
    super.dispose();
  }

  Widget _field(String key, String label, {int lines = 1}) => Padding(
    padding: const EdgeInsets.only(bottom: 18),
    child: TextFormField(
      controller: _fields[key],
      enabled: !_busy,
      minLines: lines,
      maxLines: lines == 1 ? 1 : lines + 3,
      decoration: InputDecoration(
        labelText: label,
        alignLabelWithHint: true,
        border: const OutlineInputBorder(),
      ),
      validator: (v) {
        final value = (v ?? '').trim();
        if (value.isEmpty) return 'Required';
        if (key == 'local_time' &&
            !RegExp(r'^([01]\d|2[0-3]):[0-5]\d$').hasMatch(value)) {
          return 'Use HH:mm (24-hour).';
        }
        if (key.endsWith('_date')) {
          final date = DateTime.tryParse(value);
          if (!RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(value) ||
              date == null ||
              date.toIso8601String().substring(0, 10) != value) {
            return 'Use a valid YYYY-MM-DD date.';
          }
          if (key == 'end_date' &&
              value.compareTo(_fields['start_date']!.text.trim()) < 0) {
            return 'End date must not precede start date.';
          }
        }
        return null;
      },
    ),
  );
  Future<void> _save() async {
    if (_busy || !_form.currentState!.validate()) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      FollowUpPlan plan;
      if (_creating) {
        plan = await widget.api.draft(
          widget.doctorId,
          _patientId!,
          _fields['instructions']!.text.trim(),
        );
      } else {
        final schedule = {
          for (final key in [
            'timezone',
            'local_time',
            'start_date',
            'end_date',
          ])
            key: _fields[key]!.text.trim(),
          'frequency': _frequency,
        };
        final changes = <String, dynamic>{
          if (_fields['instructions']!.text.trim() != widget.plan!.instructions)
            'instructions': _fields['instructions']!.text.trim(),
          if (schedule.entries.any(
            (e) => widget.plan!.schedule[e.key] != e.value,
          ))
            'schedule': schedule,
        };
        if (changes.isNotEmpty) await widget.api.edit(widget.plan!.id, changes);
        plan = await widget.api.plan(widget.plan!.id);
      }
      if (mounted) Navigator.pop(context, plan);
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => PopScope(
    canPop: !_busy,
    child: Scaffold(
      appBar: AppBar(
        title: Text(
          _creating ? 'New follow-up plan' : 'Edit instructions & schedule',
        ),
        automaticallyImplyLeading: !_busy,
      ),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 780),
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: Form(
              key: _form,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text(
                    widget.sample
                        ? 'Sample preview uses fixed instruction mappings, not a running Gemma model. Describe the follow-up; review the sample interpretation before activating.'
                        : 'Describe what to follow up in your own words. Gemma derives the tracking items and questions. Review the interpretation before activating.',
                  ),
                  const SizedBox(height: 24),
                  if (_creating) ...[
                    if (_loading)
                      const LinearProgressIndicator()
                    else
                      DropdownButtonFormField<int>(
                        initialValue: _patientId,
                        isExpanded: true,
                        decoration: const InputDecoration(labelText: 'Patient'),
                        items: _patients
                            .map(
                              (p) => DropdownMenuItem(
                                value: p.id,
                                child: Text(
                                  '${p.name} · ${p.phone.isEmpty ? 'Phone unavailable' : p.phone}',
                                  overflow: TextOverflow.ellipsis,
                                ),
                              ),
                            )
                            .toList(),
                        onChanged: _busy
                            ? null
                            : (v) => setState(() => _patientId = v),
                        validator: (v) =>
                            v == null ? 'Select a patient.' : null,
                      ),
                    if (!_loading && _patients.isEmpty)
                      TextButton(
                        onPressed: _loadPatients,
                        child: const Text('No patients available · Retry'),
                      ),
                    const SizedBox(height: 24),
                  ],
                  _field('instructions', 'Doctor instructions', lines: 5),
                  if (!_creating) ...[
                    const Text(
                      'Saving updated instructions regenerates the interpretation. Review it again before activating.',
                    ),
                    const SizedBox(height: 24),
                    _field('timezone', 'Timezone · e.g. America/New_York'),
                    _field('local_time', 'Call time · HH:mm'),
                    DropdownButtonFormField<String>(
                      initialValue: _frequency,
                      decoration: const InputDecoration(labelText: 'Frequency'),
                      items: const [
                        DropdownMenuItem(value: 'once', child: Text('Once')),
                        DropdownMenuItem(value: 'daily', child: Text('Daily')),
                      ],
                      onChanged: _busy
                          ? null
                          : (v) => setState(() => _frequency = v!),
                    ),
                    const SizedBox(height: 24),
                    _field('start_date', 'Start date · YYYY-MM-DD'),
                    _field('end_date', 'End date · YYYY-MM-DD'),
                  ],
                  if (_error != null)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 16),
                      child: SelectableText(_error!),
                    ),
                  if (_busy) const LinearProgressIndicator(),
                  const SizedBox(height: 16),
                  ClinicalButton(
                    onPress: _busy || _loading ? null : _save,
                    child: Text(_creating ? 'Create draft' : 'Save changes'),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    ),
  );
}
