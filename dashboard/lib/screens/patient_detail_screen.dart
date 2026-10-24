import 'package:flutter/material.dart';
import 'package:forui/forui.dart';
import '../app.dart';
import '../models/patient.dart';
import '../services/dashboard_api.dart';
import '../widgets/clinical_widgets.dart';
import 'clinical_tools.dart';
import 'record_editor.dart';
import '../widgets/vital_trends.dart';

class PatientDetailScreen extends StatefulWidget {
  final DashboardApi api;
  final int id;
  const PatientDetailScreen({super.key, required this.api, required this.id});
  @override
  State<PatientDetailScreen> createState() => _PatientDetailScreenState();
}

class _PatientDetailScreenState extends State<PatientDetailScreen> {
  late Future<Patient> _future;
  bool _loading = false, _editing = false;
  int _revision = 0;
  @override
  void initState() {
    super.initState();
    _load();
  }

  void _load() {
    if (_loading) return;
    _loading = true;
    _future = widget.api.patient(widget.id).whenComplete(() {
      if (mounted) setState(() => _loading = false);
    });
    _revision++;
  }

  void _refresh() => setState(_load);

  Future<void> _startCall(Patient patient) async {
    final result = await showDialog<_CallAction?>(
      context: context,
      builder: (ctx) => _ScheduleCallDialog(patient: patient),
    );
    if (result == null || !mounted) return;
    try {
      if (result.now) {
        final res = await widget.api.startCall(patient.id);
        if (!mounted) return;
        final mock = res['mock'] == true ? ' (mock)' : '';
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Call started$mock — session #${res['call_session_id']}')),
        );
      } else {
        final res = await widget.api.scheduleCall(
          patient.id, result.scheduledAt!.toIso8601String(), notes: result.notes);
        if (!mounted) return;
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Call scheduled for ${_fmtDt(result.scheduledAt!)} — job #${res['job_id']}')),
        );
      }
      _refresh();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Failed: $e'), backgroundColor: const Color(0xFFAC3E3E)),
      );
    }
  }

  String _fmtDt(DateTime dt) =>
      '${dt.year}-${dt.month.toString().padLeft(2, '0')}-${dt.day.toString().padLeft(2, '0')} ${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}';

  Future<void> _edit(Patient patient, {bool vital = false}) async {
    if (_editing) return;
    setState(() => _editing = true);
    final saved = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (_) =>
          RecordEditor(api: widget.api, patient: patient, vital: vital),
    );
    if (!mounted) return;
    setState(() => _editing = false);
    if (saved == true) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Record saved. Refreshing patient details.'),
        ),
      );
      _refresh();
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      backgroundColor: Colors.white,
      title: const Text('Patient record'),
      actions: [
        Padding(
          padding: const EdgeInsets.only(right: 16),
          child: FButton.icon(
            semanticsLabel: 'Refresh patient',
            onPress: _loading || _editing ? null : _refresh,
            child: const Icon(FLucideIcons.refreshCw),
          ),
        ),
      ],
    ),
    body: ListView(
      padding: EdgeInsets.all(MediaQuery.sizeOf(context).width < 600 ? 16 : 32),
      children: [
        Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 1200),
            child: AsyncContent(
              future: _future,
              retry: _refresh,
              builder: _content,
            ),
          ),
        ),
      ],
    ),
  );

  Widget _content(Patient patient) => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      SectionCard(
        title: patient.name,
        subtitle: 'Patient #${patient.id} · ${patient.category}',
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Wrap(
              spacing: 12,
              runSpacing: 12,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                RiskBadge(patient.risk),
                Text(
                  patient.phone.isEmpty ? 'Phone not recorded' : patient.phone,
                ),
                Text('Blood type: ${display(patient.data['blood_type'])}'),
              ],
            ),
            const SizedBox(height: 20),
            Wrap(
              spacing: 12,
              runSpacing: 12,
              children: [
                ClinicalButton(
                  variant: FButtonVariant.outline,
                  mainAxisSize: MainAxisSize.min,
                  prefix: const Icon(FLucideIcons.pencil),
                  onPress: _editing ? null : () => _edit(patient),
                  child: const Text('Edit clinical profile'),
                ),
                ClinicalButton(
                  mainAxisSize: MainAxisSize.min,
                  prefix: const Icon(FLucideIcons.phone),
                  onPress: _editing ? null : () => _startCall(patient),
                  child: const Text('Start check-in call'),
                ),
              ],
            ),
          ],
        ),
      ),
      const SizedBox(height: 20),
      LayoutBuilder(
        builder: (context, constraints) {
          final overview = SectionCard(
            title: 'Clinical overview',
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _line(
                  'Height',
                  '${display(patient.data['height_cm'])}${patient.data['height_cm'] == null ? '' : ' cm'}',
                ),
                _line(
                  'Weight',
                  '${display(patient.data['weight_kg'])}${patient.data['weight_kg'] == null ? '' : ' kg'}',
                ),
                const SizedBox(height: 14),
                const Text(
                  'Chronic conditions',
                  style: TextStyle(fontWeight: FontWeight.w600),
                ),
                const SizedBox(height: 10),
                Text(
                  patient.conditions.isEmpty
                      ? 'No conditions recorded.'
                      : patient.conditions.join(' · '),
                  style: const TextStyle(height: 1.6),
                ),
              ],
            ),
          );
          final medications = SectionCard(
            title: 'Medications',
            subtitle: '${patient.medications.length} recorded prescriptions',
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (patient.medications.isEmpty)
                  const Text('No medications recorded.'),
                for (final medication in patient.medications)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 16),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Wrap(
                          spacing: 10,
                          runSpacing: 6,
                          children: [
                            Text(
                              display(medication['name']),
                              style: const TextStyle(
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                            if (medication['is_new_prescription'] == true)
                              const Text(
                                'NEW PRESCRIPTION',
                                style: TextStyle(
                                  fontSize: 11,
                                  color: teal,
                                  fontWeight: FontWeight.w700,
                                ),
                              ),
                          ],
                        ),
                        const SizedBox(height: 5),
                        Text('Dose: ${display(medication['dose'])}'),
                        const SizedBox(height: 4),
                        Text(
                          'Schedule: ${display(medication['schedule'])}',
                          style: const TextStyle(color: Color(0xFF62766F)),
                        ),
                      ],
                    ),
                  ),
              ],
            ),
          );
          if (constraints.maxWidth < 750) {
            return Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [overview, const SizedBox(height: 20), medications],
            );
          }
          return Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(child: overview),
              const SizedBox(width: 20),
              Expanded(child: medications),
            ],
          );
        },
      ),
      const SizedBox(height: 20),
      if (patient.vitals.length >= 2)
        VitalTrendsSection(vitals: patient.vitals),
      if (patient.vitals.length >= 2)
        const SizedBox(height: 20),
      SectionCard(
        title: 'Recent vitals',
        subtitle: 'Recorded measurements · Most recent first',
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (patient.vitals.isEmpty) const Text('No vitals recorded.'),
            for (final vital in _sortedVitals(patient.vitals))
              Padding(
                padding: const EdgeInsets.only(bottom: 16),
                child: Wrap(
                  alignment: WrapAlignment.spaceBetween,
                  spacing: 20,
                  runSpacing: 6,
                  children: [
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          display(vital['type']).replaceAll('_', ' '),
                          style: const TextStyle(fontWeight: FontWeight.w600),
                        ),
                        const SizedBox(height: 5),
                        Text(
                          timestamp(vital['recorded_at']),
                          style: const TextStyle(
                            fontSize: 12,
                            color: Color(0xFF62766F),
                          ),
                        ),
                      ],
                    ),
                    Text(
                      '${display(vital['value'])} ${vital['unit'] ?? ''}',
                      style: const TextStyle(
                        fontSize: 20,
                        fontWeight: FontWeight.w500,
                        color: teal,
                      ),
                    ),
                  ],
                ),
              ),
            const SizedBox(height: 8),
            Align(
              alignment: Alignment.centerLeft,
              child: ClinicalButton(
                mainAxisSize: MainAxisSize.min,
                variant: FButtonVariant.outline,
                prefix: const Icon(FLucideIcons.plus),
                onPress: _editing ? null : () => _edit(patient, vital: true),
                child: const Text('Record a vital'),
              ),
            ),
          ],
        ),
      ),
      const SizedBox(height: 20),
      SectionCard(
        title: 'Report history',
        subtitle: 'Summaries from recorded conversations',
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (patient.reports.isEmpty) const Text('No reports recorded.'),
            for (final report in patient.reports)
              Padding(
                padding: const EdgeInsets.only(bottom: 20),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Wrap(
                      spacing: 12,
                      runSpacing: 6,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      children: [
                        Text(
                          timestamp(report['created_at']),
                          style: const TextStyle(
                            color: Color(0xFF62766F),
                            fontSize: 12,
                          ),
                        ),
                        if (report['escalated'] == true)
                          const RiskBadge('urgent'),
                      ],
                    ),
                    const SizedBox(height: 10),
                    SelectableText(
                      display(report['summary']),
                      style: const TextStyle(height: 1.7),
                    ),
                  ],
                ),
              ),
          ],
        ),
      ),
      const SizedBox(height: 20),
      ClinicalTools(
        key: ValueKey(_revision),
        api: widget.api,
        patientId: patient.id,
      ),
      const SizedBox(height: 20),
      const SectionCard(
        title: 'Doctor-directed follow-up',
        child: Text(
          'Calling, scheduling and saving follow-up instructions are not connected to this server. Review recommendations here; arrange follow-up through your existing care workflow.',
          style: TextStyle(height: 1.6, color: Color(0xFF62766F)),
        ),
      ),
    ],
  );

  Widget _line(String label, String value) => Padding(
    padding: const EdgeInsets.only(bottom: 12),
    child: Wrap(
      spacing: 16,
      children: [
        Text(label, style: const TextStyle(color: Color(0xFF62766F))),
        Text(value, style: const TextStyle(fontWeight: FontWeight.w600)),
      ],
    ),
  );
  List<Json> _sortedVitals(List<Json> vitals) => [...vitals]
    ..sort(
      (a, b) =>
          '${b['recorded_at'] ?? ''}'.compareTo('${a['recorded_at'] ?? ''}'),
    );
}

// ---------------------------------------------------------------------------
// Schedule / Call Now dialog
// ---------------------------------------------------------------------------

class _CallAction {
  final bool now;
  final DateTime? scheduledAt;
  final String? notes;
  const _CallAction.callNow() : now = true, scheduledAt = null, notes = null;
  const _CallAction.schedule(this.scheduledAt, this.notes) : now = false;
}

class _ScheduleCallDialog extends StatefulWidget {
  final Patient patient;
  const _ScheduleCallDialog({required this.patient});
  @override
  State<_ScheduleCallDialog> createState() => _ScheduleCallDialogState();
}

class _ScheduleCallDialogState extends State<_ScheduleCallDialog> {
  DateTime _date = DateTime.now().add(const Duration(days: 1));
  TimeOfDay _time = const TimeOfDay(hour: 9, minute: 0);
  final _notesCtrl = TextEditingController();

  @override
  void dispose() {
    _notesCtrl.dispose();
    super.dispose();
  }

  Future<void> _pickDate() async {
    final picked = await showDatePicker(
      context: context,
      initialDate: _date,
      firstDate: DateTime.now(),
      lastDate: DateTime.now().add(const Duration(days: 90)),
    );
    if (picked != null) setState(() => _date = picked);
  }

  Future<void> _pickTime() async {
    final picked = await showTimePicker(context: context, initialTime: _time);
    if (picked != null) setState(() => _time = picked);
  }

  String get _dateLabel =>
      '${_date.year}-${_date.month.toString().padLeft(2, '0')}-${_date.day.toString().padLeft(2, '0')}';
  String get _timeLabel =>
      '${_time.hour.toString().padLeft(2, '0')}:${_time.minute.toString().padLeft(2, '0')}';

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text('Call ${widget.patient.name}'),
    content: SizedBox(
      width: 360,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text('Phone: ${widget.patient.phone}',
              style: const TextStyle(color: Color(0xFF62766F))),
          const SizedBox(height: 16),
          const Text('Schedule for:', style: TextStyle(fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(
                child: OutlinedButton.icon(
                  icon: const Icon(Icons.calendar_today, size: 16),
                  label: Text(_dateLabel),
                  onPressed: _pickDate,
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: OutlinedButton.icon(
                  icon: const Icon(Icons.access_time, size: 16),
                  label: Text(_timeLabel),
                  onPressed: _pickTime,
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _notesCtrl,
            decoration: const InputDecoration(
              labelText: 'Notes (optional)',
              hintText: 'e.g. Check BP and medication side effects',
              border: OutlineInputBorder(),
              isDense: true,
            ),
            maxLines: 2,
          ),
        ],
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('Cancel'),
      ),
      OutlinedButton.icon(
        icon: const Icon(Icons.schedule, size: 16),
        label: Text('Schedule · $_dateLabel $_timeLabel'),
        onPressed: () {
          final scheduled = DateTime(
            _date.year, _date.month, _date.day, _time.hour, _time.minute);
          Navigator.pop(context, _CallAction.schedule(
            scheduled, _notesCtrl.text.trim().isEmpty ? null : _notesCtrl.text.trim()));
        },
      ),
      FilledButton.icon(
        icon: const Icon(Icons.phone, size: 16),
        label: const Text('Call now'),
        onPressed: () => Navigator.pop(context, const _CallAction.callNow()),
      ),
    ],
  );
}
