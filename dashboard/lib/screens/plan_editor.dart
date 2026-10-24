import 'package:flutter/material.dart';
import 'package:forui/forui.dart';
import '../models/follow_up_plan.dart';
import '../services/plan_api.dart';
import '../widgets/workspace_widgets.dart';
import 'patient_editor.dart';

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
  bool _scheduling = false;
  bool get _creating => widget.plan == null;
  @override
  void initState() {
    super.initState();
    final p = widget.plan;
    _scheduling = p?.schedule.isNotEmpty ?? false;
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

  Future<void> _newPatient() async {
    final patient = await Navigator.of(context).push<PlanPatient>(
      MaterialPageRoute(
        builder: (_) =>
            PatientEditor(api: widget.api, doctorId: widget.doctorId),
      ),
    );
    if (!mounted || patient == null) return;
    setState(() {
      _patients = [..._patients.where((p) => p.id != patient.id), patient];
      _patientId = patient.id;
    });
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
    child: FTextFormField(
      control: FTextFieldControl.managed(controller: _fields[key]),
      enabled: !_busy,
      minLines: lines,
      maxLines: lines == 1 ? 1 : lines + 3,
      label: Text(label),
      validator: (v) {
        final value = (v ?? '').trim();
        if (key != 'instructions' && !_scheduling) return null;
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
        final schedule = _scheduling
            ? {
                for (final key in [
                  'timezone',
                  'local_time',
                  'start_date',
                  'end_date',
                ])
                  key: _fields[key]!.text.trim(),
                'frequency': _frequency,
              }
            : null;
        final changes = <String, dynamic>{
          if (_fields['instructions']!.text.trim() != widget.plan!.instructions)
            'instructions': _fields['instructions']!.text.trim(),
          if (schedule == null
              ? widget.plan!.schedule.isNotEmpty
              : schedule.entries.any(
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
    child: FScaffold(
      child: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 780),
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: Form(
              key: _form,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Row(
                    children: [
                      WorkspaceButton(
                        onPress: _busy ? null : () => Navigator.pop(context),
                        variant: FButtonVariant.ghost,
                        prefix: const Icon(FLucideIcons.arrowLeft, size: 16),
                        child: const Text('Back'),
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Text(
                          _creating
                              ? 'New follow-up plan'
                              : 'Edit instructions & schedule',
                          style: const TextStyle(
                            fontSize: 21,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 20),
                  const Text(
                    'Describe what to track in your own words. Review the interpretation, then call immediately or add a schedule.',
                  ),
                  const SizedBox(height: 24),
                  if (_creating) ...[
                    Align(
                      alignment: Alignment.centerLeft,
                      child: WorkspaceButton(
                        onPress: _busy ? null : _newPatient,
                        variant: FButtonVariant.outline,
                        prefix: const Icon(FLucideIcons.plus, size: 14),
                        child: const Text('New patient'),
                      ),
                    ),
                    const SizedBox(height: 18),
                    if (_loading)
                      const FProgress()
                    else
                      FSelect<int>(
                        label: const Text('Patient'),
                        hint: 'Select a patient',
                        enabled: !_busy,
                        control: FSelectControl.lifted(
                          value: _patientId,
                          onChange: (v) => setState(() => _patientId = v),
                        ),
                        items: {
                          for (final p in _patients)
                            '${p.name} · ${p.phone.isEmpty ? 'Phone unavailable' : p.phone} · #${p.id}':
                                p.id,
                        },
                        validator: (v) =>
                            v == null ? 'Select a patient.' : null,
                      ),
                    if (!_loading && _patients.isEmpty)
                      WorkspaceButton(
                        onPress: _loadPatients,
                        child: const Text('No patients available · Retry'),
                      ),
                    const SizedBox(height: 24),
                  ],
                  _field('instructions', 'Doctor instructions', lines: 5),
                  if (!_creating) ...[
                    const Text(
                      'Saving updated instructions regenerates the interpretation. Review it again before calling.',
                    ),
                    const SizedBox(height: 24),
                    Align(
                      alignment: Alignment.centerLeft,
                      child: WorkspaceButton(
                        variant: FButtonVariant.outline,
                        onPress: _busy
                            ? null
                            : () => setState(() => _scheduling = !_scheduling),
                        child: Text(
                          _scheduling
                              ? 'Remove optional schedule'
                              : 'Add optional schedule',
                        ),
                      ),
                    ),
                    if (_scheduling) ...[
                      const SizedBox(height: 18),
                      _field('timezone', 'Timezone · e.g. America/New_York'),
                      _field('local_time', 'Call time · HH:mm'),
                      FSelect<String>(
                        label: const Text('Frequency'),
                        enabled: !_busy,
                        items: const {'Once': 'once', 'Daily': 'daily'},
                        control: FSelectControl.lifted(
                          value: _frequency,
                          onChange: (v) {
                            if (v != null) setState(() => _frequency = v);
                          },
                        ),
                      ),
                      const SizedBox(height: 24),
                      _field('start_date', 'Start date · YYYY-MM-DD'),
                      _field('end_date', 'End date · YYYY-MM-DD'),
                    ],
                  ],
                  if (_error != null)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 16),
                      child: SelectableText(_error!),
                    ),
                  if (_busy) const FProgress(),
                  const SizedBox(height: 16),
                  if (_busy)
                    const Padding(
                      padding: EdgeInsets.only(bottom: 16),
                      child: Text(
                        'Gemma is interpreting your instructions. This can take up to four minutes. Keep this window open; no call is being placed.',
                      ),
                    ),
                  Align(
                    alignment: Alignment.centerLeft,
                    child: WorkspaceButton(
                      onPress: _busy || _loading ? null : _save,
                      child: Text(_creating ? 'Create draft' : 'Save changes'),
                    ),
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
