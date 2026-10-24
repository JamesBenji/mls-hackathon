import 'package:flutter/material.dart';
import '../models/patient.dart';
import '../services/dashboard_api.dart';
import '../widgets/clinical_widgets.dart';

class RecordEditor extends StatefulWidget {
  final DashboardApi api;
  final Patient patient;
  final bool vital;
  const RecordEditor({
    super.key,
    required this.api,
    required this.patient,
    required this.vital,
  });
  @override
  State<RecordEditor> createState() => _RecordEditorState();
}

class _RecordEditorState extends State<RecordEditor> {
  final _form = GlobalKey<FormState>();
  late final _height = TextEditingController(
    text: '${widget.patient.data['height_cm'] ?? ''}',
  );
  late final _weight = TextEditingController(
    text: '${widget.patient.data['weight_kg'] ?? ''}',
  );
  late final _conditions = TextEditingController(
    text: widget.patient.conditions.join(', '),
  );
  final _value = TextEditingController();
  late String _risk = widget.patient.risk;
  String _type = 'bp_systolic';
  bool _busy = false;
  String? _error;
  static const _units = {
    'bp_systolic': 'mmHg',
    'bp_diastolic': 'mmHg',
    'blood_sugar': 'mg/dL',
    'weight': 'kg',
    'heart_rate': 'bpm',
    'temperature': '°C',
    'oxygen_saturation': '%',
  };
  @override
  void dispose() {
    _height.dispose();
    _weight.dispose();
    _conditions.dispose();
    _value.dispose();
    super.dispose();
  }

  String? _number(
    String? text,
    double min,
    double max, {
    bool optional = false,
  }) {
    if (optional && (text == null || text.trim().isEmpty)) return null;
    final number = double.tryParse(text ?? '');
    return number == null || !number.isFinite || number < min || number > max
        ? 'Enter a number between $min and $max.'
        : null;
  }

  Future<void> _save() async {
    if (_busy || !_form.currentState!.validate()) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      if (widget.vital) {
        await widget.api.addVital(widget.patient.id, {
          'type': _type,
          'value': double.parse(_value.text),
          'unit': _units[_type],
        });
      } else {
        await widget.api.updatePatient(widget.patient.id, {
          'height_cm': double.tryParse(_height.text),
          'weight_kg': double.tryParse(_weight.text),
          'risk_level': _risk,
          'chronic_conditions': _conditions.text
              .split(',')
              .map((s) => s.trim())
              .where((s) => s.isNotEmpty)
              .toSet()
              .toList(),
        });
      }
      if (mounted) Navigator.of(context).pop(true);
    } catch (error) {
      if (mounted) setState(() => _error = '$error');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => PopScope(
    canPop: !_busy,
    child: AlertDialog(
      title: Text(widget.vital ? 'Record a vital' : 'Edit clinical profile'),
      content: SizedBox(
        width: 440,
        child: SingleChildScrollView(
          child: Form(
            key: _form,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                if (widget.vital) ...[
                  DropdownButtonFormField<String>(
                    initialValue: _type,
                    isExpanded: true,
                    decoration: const InputDecoration(labelText: 'Measurement'),
                    items: _units.keys
                        .map(
                          (type) => DropdownMenuItem(
                            value: type,
                            child: Text(type.replaceAll('_', ' ')),
                          ),
                        )
                        .toList(),
                    onChanged: _busy
                        ? null
                        : (value) => setState(() => _type = value!),
                  ),
                  const SizedBox(height: 16),
                  TextFormField(
                    controller: _value,
                    enabled: !_busy,
                    keyboardType: const TextInputType.numberWithOptions(
                      decimal: true,
                    ),
                    decoration: InputDecoration(
                      labelText: 'Value (${_units[_type]})',
                    ),
                    validator: (text) => _number(
                      text,
                      .1,
                      _type == 'oxygen_saturation' ? 100 : 1000,
                    ),
                  ),
                  const SizedBox(height: 12),
                  const Text(
                    'Recorded now as a manual measurement. Check the type, value and unit before saving.',
                  ),
                ] else ...[
                  DropdownButtonFormField<String>(
                    initialValue: _risk,
                    decoration: const InputDecoration(
                      labelText: 'Clinical risk',
                    ),
                    items:
                        <String>{widget.patient.risk, 'low', 'medium', 'high'}
                            .map(
                              (risk) => DropdownMenuItem(
                                value: risk,
                                child: Text(risk),
                              ),
                            )
                            .toList(),
                    onChanged: _busy
                        ? null
                        : (value) => setState(() => _risk = value!),
                  ),
                  const SizedBox(height: 16),
                  TextFormField(
                    controller: _height,
                    enabled: !_busy,
                    keyboardType: const TextInputType.numberWithOptions(
                      decimal: true,
                    ),
                    decoration: const InputDecoration(
                      labelText: 'Height (cm)',
                      helperText: 'Leave empty to clear.',
                    ),
                    validator: (text) => _number(text, 20, 300, optional: true),
                  ),
                  const SizedBox(height: 16),
                  TextFormField(
                    controller: _weight,
                    enabled: !_busy,
                    keyboardType: const TextInputType.numberWithOptions(
                      decimal: true,
                    ),
                    decoration: const InputDecoration(
                      labelText: 'Weight (kg)',
                      helperText: 'Leave empty to clear.',
                    ),
                    validator: (text) => _number(text, .1, 700, optional: true),
                  ),
                  const SizedBox(height: 16),
                  TextFormField(
                    controller: _conditions,
                    enabled: !_busy,
                    maxLines: 3,
                    maxLength: 2000,
                    decoration: const InputDecoration(
                      labelText: 'Chronic conditions',
                      helperText: 'Separate conditions with commas.',
                    ),
                  ),
                ],
                if (_error != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 16),
                    child: Text(
                      _error!,
                      style: const TextStyle(color: Colors.red),
                    ),
                  ),
              ],
            ),
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: _busy ? null : () => Navigator.of(context).pop(false),
          child: const Text('Cancel'),
        ),
        ClinicalButton(
          mainAxisSize: MainAxisSize.min,
          onPress: _busy ? null : _save,
          child: Text(_busy ? 'Saving…' : 'Save record'),
        ),
      ],
    ),
  );
}
