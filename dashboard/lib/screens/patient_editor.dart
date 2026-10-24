import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:forui/forui.dart';

import '../models/follow_up_plan.dart';
import '../services/plan_api.dart';
import '../widgets/workspace_widgets.dart';

class PatientEditor extends StatefulWidget {
  const PatientEditor({super.key, required this.api, required this.doctorId});
  final PlanApi api;
  final int doctorId;

  @override
  State<PatientEditor> createState() => _PatientEditorState();
}

class _PatientEditorState extends State<PatientEditor> {
  final _form = GlobalKey<FormState>();
  final _name = TextEditingController();
  final _phone = TextEditingController();
  PlanPatient? _patient;
  String? _token;
  String? _error;
  bool _busy = false;
  bool _copied = false;

  @override
  void dispose() {
    _name.dispose();
    _phone.dispose();
    super.dispose();
  }

  Future<void> _create() async {
    if (!_form.currentState!.validate()) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final patient = await widget.api.createPatient(
        widget.doctorId,
        _name.text.trim(),
        _phone.text.trim(),
      );
      if (mounted) setState(() => _patient = patient);
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _pair() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final pairing = await widget.api.pairPatient(
        widget.doctorId,
        _patient!.id,
      );
      if (mounted) setState(() => _token = pairing['access_token'] as String);
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
          constraints: const BoxConstraints(maxWidth: 620),
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: Form(
              key: _form,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Text(
                    _patient == null
                        ? 'New patient'
                        : '${_patient!.name} is ready',
                    style: const TextStyle(
                      fontSize: 24,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  const SizedBox(height: 24),
                  if (_patient == null) ...[
                    FTextFormField(
                      control: FTextFieldControl.managed(controller: _name),
                      enabled: !_busy,
                      label: const Text('Patient name'),
                      validator: (v) => v == null || v.trim().isEmpty
                          ? 'Enter a name.'
                          : null,
                    ),
                    const SizedBox(height: 18),
                    FTextFormField(
                      control: FTextFieldControl.managed(controller: _phone),
                      enabled: !_busy,
                      label: const Text('Phone number including country code'),
                      hint: '+49 …',
                      validator: (v) =>
                          RegExp(r'^\+[1-9][0-9]{6,14}$').hasMatch(
                            (v ?? '').replaceAll(RegExp(r'[\s\-().]'), ''),
                          )
                          ? null
                          : 'Include the country code, for example +49.',
                    ),
                  ] else ...[
                    Text('Patient #${_patient!.id} · ${_patient!.phone}'),
                    const SizedBox(height: 18),
                    const Text(
                      'For photos, connect this patient in Medicall on their phone before dispatching the call.',
                    ),
                    const SizedBox(height: 14),
                    if (_token == null)
                      WorkspaceButton(
                        onPress: _busy ? null : _pair,
                        child: const Text('Create private phone pairing token'),
                      )
                    else ...[
                      const Text(
                        'Enter this token in the phone app. It grants access to this patient; share it privately.',
                      ),
                      const SizedBox(height: 10),
                      SelectableText(_token!),
                      const SizedBox(height: 10),
                      WorkspaceButton(
                        variant: FButtonVariant.outline,
                        onPress: () async {
                          await Clipboard.setData(ClipboardData(text: _token!));
                          if (mounted) setState(() => _copied = true);
                        },
                        child: Text(_copied ? 'Copied' : 'Copy token'),
                      ),
                    ],
                  ],
                  if (_error != null)
                    Padding(
                      padding: const EdgeInsets.only(top: 16),
                      child: Text(_error!),
                    ),
                  if (_busy)
                    const Padding(
                      padding: EdgeInsets.all(12),
                      child: FProgress(),
                    ),
                  const SizedBox(height: 24),
                  Wrap(
                    spacing: 10,
                    runSpacing: 10,
                    children: [
                      WorkspaceButton(
                        variant: FButtonVariant.outline,
                        onPress: _busy
                            ? null
                            : () => Navigator.pop(context, _patient),
                        child: Text(
                          _patient == null
                              ? 'Cancel'
                              : 'Continue to tracking plan',
                        ),
                      ),
                      if (_patient == null)
                        WorkspaceButton(
                          onPress: _busy ? null : _create,
                          child: const Text('Create patient'),
                        ),
                    ],
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
