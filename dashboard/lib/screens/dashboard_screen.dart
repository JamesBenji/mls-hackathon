import 'package:flutter/material.dart';
import 'package:forui/forui.dart';
import '../app.dart';
import '../models/patient.dart';
import '../services/dashboard_api.dart';
import '../widgets/clinical_widgets.dart';
import 'patient_detail_screen.dart';
import 'triage_panel.dart';

class DashboardScreen extends StatefulWidget {
  final DashboardApi api;
  const DashboardScreen({super.key, required this.api});
  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends State<DashboardScreen> {
  late Future<List<Patient>> _patients;
  bool _loading = false;
  String _search = '', _risk = 'all';
  int _section = 0;
  DateTime? _updated;

  @override
  void initState() {
    super.initState();
    _load();
  }

  void _load() {
    if (_loading) return;
    _loading = true;
    _patients = widget.api
        .patients()
        .then((patients) {
          if (mounted) setState(() => _updated = DateTime.now());
          return patients;
        })
        .whenComplete(() {
          if (mounted) setState(() => _loading = false);
        });
  }

  void _refresh() => setState(_load);

  Widget _navigation() => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      const Padding(
        padding: EdgeInsets.fromLTRB(8, 14, 8, 34),
        child: Row(
          children: [
            Icon(FLucideIcons.heartPulse, color: teal, size: 30),
            SizedBox(width: 10),
            Flexible(
              child: Text(
                'guardian',
                style: TextStyle(
                  fontSize: 25,
                  color: ink,
                  fontWeight: FontWeight.w700,
                  letterSpacing: -1,
                ),
              ),
            ),
          ],
        ),
      ),
      const Padding(
        padding: EdgeInsets.all(10),
        child: Text(
          'WORKSPACE',
          style: TextStyle(
            fontSize: 10,
            color: Color(0xFF62766F),
            letterSpacing: 1.8,
          ),
        ),
      ),
      for (final (index, label, icon) in [
        (0, 'Patients', FLucideIcons.users),
        (1, 'Triage review', FLucideIcons.clipboardCheck),
      ])
        Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: ClinicalButton(
            variant: _section == index
                ? FButtonVariant.secondary
                : FButtonVariant.ghost,
            mainAxisAlignment: MainAxisAlignment.start,
            prefix: Icon(icon),
            onPress: () => setState(() => _section = index),
            child: Text(label),
          ),
        ),
      const Spacer(),
      const SectionCard(
        title: 'Clinical workspace',
        child: Text(
          'Review records and decisions. Follow-up remains doctor-directed.',
          style: TextStyle(height: 1.6, color: Color(0xFF62766F)),
        ),
      ),
    ],
  );

  @override
  Widget build(BuildContext context) => Scaffold(
    body: SafeArea(
      child: LayoutBuilder(
        builder: (context, constraints) {
          final wide = constraints.maxWidth >= 1000;
          return Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (wide)
                Container(
                  width: 248,
                  padding: const EdgeInsets.all(20),
                  decoration: const BoxDecoration(
                    color: Colors.white,
                    border: Border(right: BorderSide(color: Color(0xFFDCE8E3))),
                  ),
                  child: _navigation(),
                ),
              Expanded(
                child: Column(
                  children: [
                    Container(
                      color: Colors.white,
                      padding: EdgeInsets.symmetric(
                        horizontal: wide ? 32 : 20,
                        vertical: 18,
                      ),
                      child: Row(
                        children: [
                          const Icon(
                            FLucideIcons.stethoscope,
                            color: teal,
                            size: 20,
                          ),
                          const SizedBox(width: 10),
                          const Expanded(
                            child: Text(
                              'Care team / Clinical overview',
                              style: TextStyle(color: Color(0xFF62766F)),
                            ),
                          ),
                          if (wide)
                            const Text(
                              'Doctor workspace',
                              style: TextStyle(fontWeight: FontWeight.w600),
                            ),
                        ],
                      ),
                    ),
                    if (!wide)
                      Padding(
                        padding: const EdgeInsets.fromLTRB(16, 14, 16, 0),
                        child: Row(
                          children: [
                            Expanded(
                              child: ClinicalButton(
                                variant: _section == 0
                                    ? FButtonVariant.secondary
                                    : FButtonVariant.ghost,
                                onPress: () => setState(() => _section = 0),
                                child: const Text('Patients'),
                              ),
                            ),
                            Expanded(
                              child: ClinicalButton(
                                variant: _section == 1
                                    ? FButtonVariant.secondary
                                    : FButtonVariant.ghost,
                                onPress: () => setState(() => _section = 1),
                                child: const Text('Triage review'),
                              ),
                            ),
                          ],
                        ),
                      ),
                    Expanded(
                      child: _section == 1
                          ? TriagePanel(api: widget.api, patients: _patients)
                          : ListView(
                              padding: EdgeInsets.all(wide ? 32 : 20),
                              children: [
                                Wrap(
                                  alignment: WrapAlignment.spaceBetween,
                                  crossAxisAlignment: WrapCrossAlignment.center,
                                  spacing: 20,
                                  runSpacing: 16,
                                  children: [
                                    const Column(
                                      crossAxisAlignment:
                                          CrossAxisAlignment.start,
                                      children: [
                                        Text(
                                          'Patient overview',
                                          style: TextStyle(
                                            fontSize: 30,
                                            fontWeight: FontWeight.w600,
                                            color: ink,
                                            letterSpacing: -.8,
                                          ),
                                        ),
                                        SizedBox(height: 8),
                                        Text(
                                          'Your patients, prioritized by clinical risk.',
                                          style: TextStyle(
                                            color: Color(0xFF62766F),
                                          ),
                                        ),
                                      ],
                                    ),
                                    ClinicalButton(
                                      variant: FButtonVariant.outline,
                                      mainAxisSize: MainAxisSize.min,
                                      prefix: const Icon(
                                        FLucideIcons.refreshCw,
                                      ),
                                      onPress: _loading ? null : _refresh,
                                      child: const Text('Refresh records'),
                                    ),
                                  ],
                                ),
                                const SizedBox(height: 26),
                                AsyncContent(
                                  future: _patients,
                                  retry: _refresh,
                                  builder: (patients) => _patientContent(
                                    patients,
                                    constraints.maxWidth,
                                  ),
                                ),
                              ],
                            ),
                    ),
                  ],
                ),
              ),
            ],
          );
        },
      ),
    ),
  );

  Widget _patientContent(List<Patient> patients, double width) {
    final filtered = patients
        .where(
          (p) =>
              (_risk == 'all' || p.risk == _risk) &&
              '${p.name} ${p.phone} ${p.id} ${p.conditions.join(' ')}'
                  .toLowerCase()
                  .contains(_search.toLowerCase()),
        )
        .toList();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        LayoutBuilder(
          builder: (context, c) => Wrap(
            spacing: 14,
            runSpacing: 14,
            children: [
              for (final (label, count, icon) in [
                ('Total patients', patients.length, FLucideIcons.users),
                (
                  'High risk',
                  patients.where((p) => p.risk == 'high').length,
                  FLucideIcons.shieldAlert,
                ),
                (
                  'Medium risk',
                  patients.where((p) => p.risk == 'medium').length,
                  FLucideIcons.activity,
                ),
              ])
                SizedBox(
                  width: c.maxWidth < 600 ? c.maxWidth : (c.maxWidth - 28) / 3,
                  child: SectionCard(
                    title: label,
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Text(
                          '$count',
                          style: const TextStyle(
                            fontSize: 32,
                            fontWeight: FontWeight.w600,
                            color: ink,
                          ),
                        ),
                        Icon(icon, color: teal, size: 26),
                      ],
                    ),
                  ),
                ),
            ],
          ),
        ),
        const SizedBox(height: 26),
        SectionCard(
          title: 'Patient directory',
          subtitle: _updated == null
              ? null
              : 'Last refreshed ${timestamp(_updated!.toIso8601String())} · Manual refresh',
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(
                onChanged: (value) => setState(() => _search = value),
                decoration: const InputDecoration(
                  prefixIcon: Icon(FLucideIcons.search),
                  hintText: 'Search name, condition or ID',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 14),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  for (final risk in [
                    'all',
                    'high',
                    'medium',
                    'low',
                    'unknown',
                  ])
                    ClinicalButton(
                      size: FButtonSizeVariant.sm,
                      mainAxisSize: MainAxisSize.min,
                      variant: _risk == risk
                          ? FButtonVariant.secondary
                          : FButtonVariant.ghost,
                      onPress: () => setState(() => _risk = risk),
                      child: Text(
                        risk == 'all'
                            ? 'All patients'
                            : '${risk[0].toUpperCase()}${risk.substring(1)}',
                      ),
                    ),
                ],
              ),
              const SizedBox(height: 18),
              Text(
                '${filtered.length} patients · Highest risk first',
                style: const TextStyle(color: Color(0xFF62766F), fontSize: 12),
              ),
              const SizedBox(height: 12),
              if (filtered.isEmpty)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 30),
                  child: Text(
                    patients.isEmpty
                        ? 'No patients recorded yet.'
                        : 'No patients match your search or filter.',
                  ),
                ),
              for (final patient in filtered)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 5),
                  child: FButton.raw(
                    variant: FButtonVariant.ghost,
                    onPress: () async {
                      await Navigator.of(context).push(
                        MaterialPageRoute<void>(
                          builder: (_) => PatientDetailScreen(
                            api: widget.api,
                            id: patient.id,
                          ),
                        ),
                      );
                      if (mounted) _refresh();
                    },
                    child: Padding(
                      padding: const EdgeInsets.symmetric(
                        vertical: 15,
                        horizontal: 10,
                      ),
                      child: Row(
                        children: [
                          CircleAvatar(
                            backgroundColor: mint,
                            foregroundColor: teal,
                            child: Text(patient.initials),
                          ),
                          const SizedBox(width: 14),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  patient.name,
                                  style: const TextStyle(
                                    fontWeight: FontWeight.w600,
                                    fontSize: 15,
                                  ),
                                ),
                                const SizedBox(height: 5),
                                Text(
                                  '${patient.category} · #${patient.id}',
                                  style: const TextStyle(
                                    fontSize: 12,
                                    color: Color(0xFF62766F),
                                  ),
                                ),
                                if (width >= 700)
                                  Padding(
                                    padding: const EdgeInsets.only(top: 5),
                                    child: Text(
                                      patient.conditions.isEmpty
                                          ? 'No conditions recorded'
                                          : patient.conditions.join(' · '),
                                      style: const TextStyle(
                                        fontSize: 12,
                                        color: Color(0xFF62766F),
                                      ),
                                    ),
                                  ),
                              ],
                            ),
                          ),
                          const SizedBox(width: 8),
                          RiskBadge(patient.risk),
                          if (width >= 700)
                            const Padding(
                              padding: EdgeInsets.only(left: 20),
                              child: Icon(
                                FLucideIcons.chevronRight,
                                size: 18,
                                color: teal,
                              ),
                            ),
                        ],
                      ),
                    ),
                  ),
                ),
            ],
          ),
        ),
      ],
    );
  }
}
