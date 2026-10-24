import 'dart:math';
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:forui/forui.dart';
import '../models/follow_up_plan.dart';
import '../services/plan_api.dart';
import '../widgets/workspace_widgets.dart';
import 'plan_detail.dart';
import 'plan_editor.dart';

class FollowUpWorkspace extends StatefulWidget {
  final PlanApi api;
  final int doctorId;
  final bool sample;
  final VoidCallback? onDisconnect;
  const FollowUpWorkspace({
    super.key,
    required this.api,
    required this.doctorId,
    required this.sample,
    this.onDisconnect,
  });
  @override
  State<FollowUpWorkspace> createState() => _FollowUpWorkspaceState();
}

class _FollowUpWorkspaceState extends State<FollowUpWorkspace> {
  List<FollowUpPlan> _plans = [];
  FollowUpPlan? _selected;
  bool _loading = true, _busy = false, _confirming = false;
  String? _error, _notice;
  String _tab = 'Active', _search = '';
  final _masterScroll = ScrollController();
  final _detailScroll = ScrollController();
  final _mobileScroll = ScrollController();
  Timer? _refreshTimer;
  bool _polling = false;
  @override
  void initState() {
    super.initState();
    _load();
    if (!widget.sample) {
      _refreshTimer = Timer.periodic(
        const Duration(seconds: 8),
        (_) => _pollSelected(),
      );
    }
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    _masterScroll.dispose();
    _detailScroll.dispose();
    _mobileScroll.dispose();
    super.dispose();
  }

  bool _matches(FollowUpPlan p, String tab) => tab == 'Active'
      ? p.status == 'active'
      : tab == 'Finished'
      ? p.status == 'finished'
      : p.needsReview;
  List<FollowUpPlan> get _visible => _plans
      .where(
        (p) =>
            _matches(p, _tab) &&
            '${p.name} ${p.instructions}'.toLowerCase().contains(
              _search.toLowerCase(),
            ),
      )
      .toList();
  bool get _locked => _loading || _busy;

  Future<void> _pollSelected() async {
    if (_locked || _polling || _selected == null) return;
    final id = _selected!.id;
    _polling = true;
    try {
      final plan = await widget.api.plan(id);
      if (mounted && !_locked && _selected?.id == id) {
        setState(() => _selected = plan);
      }
    } catch (_) {
      // Keep existing results; the explicit Refresh action reports connection errors.
    } finally {
      _polling = false;
    }
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final plans = await widget.api.plans();
      if (!mounted) return;
      setState(() {
        _plans = plans;
        _loading = false;
      });
      if (_selected == null && _visible.isNotEmpty) {
        await _select(_visible.first);
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _loading = false;
          _error = '$e';
        });
      }
    }
  }

  Future<void> _refresh() async {
    await _load();
    if (mounted && _selected != null) await _select(_selected!);
  }

  Future<void> _select(FollowUpPlan p) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final plan = await widget.api.plan(p.id);
      if (!mounted) return;
      setState(() => _selected = plan);
      if (_detailScroll.hasClients) _detailScroll.jumpTo(0);
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _switchTab(String tab) {
    setState(() {
      _tab = tab;
      _selected = null;
      _notice = null;
    });
    if (_visible.isNotEmpty) _select(_visible.first);
  }

  Future<void> _action(String action) async {
    if (_locked || _selected == null) return;
    final approvedPlan = _selected!;
    setState(() {
      _busy = true;
      _confirming = true;
      _error = null;
      _notice = null;
    });
    try {
      await _performAction(action, approvedPlan);
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    } finally {
      if (mounted) {
        setState(() {
          _busy = false;
          _confirming = false;
        });
      }
    }
  }

  Future<void> _performAction(String action, FollowUpPlan approvedPlan) async {
    String? destination;
    if (['call', 'activate', 'finish'].contains(action)) {
      final p = approvedPlan;
      if (action == 'call') {
        try {
          final patients = await widget.api.patients();
          destination = patients
              .firstWhere((patient) => patient.id == p.patientId)
              .phone;
        } catch (e) {
          throw PlanApiException('Cannot confirm the destination number. $e');
        }
        if (!mounted) return;
      }
      final confirmed = await confirmPlanAction(
        context,
        title: action == 'activate'
            ? 'Activate this plan?'
            : action == 'finish'
            ? 'Finish this plan?'
            : 'Call ${p.name} now?',
        body: action == 'activate'
            ? '${p.name}\n${p.instructions}\n\n${p.schedule['frequency']} at ${p.schedule['local_time']} · ${p.schedule['timezone']}\n${p.schedule['start_date']} → ${p.schedule['end_date']}\n\nConfirm you have reviewed the instructions, tracking and schedule.'
            : action == 'finish'
            ? 'Finish future follow-up for ${p.name}? A call already in progress is not ended.'
            : '${p.name}\n$destination\n\n${p.instructions}\n\nQuestions:\n${(p.data['questions'] as List? ?? []).join('\n')}\n\nPhotos: ${p.data['photo_policy']}\nUses the agent’s configured duration limit. One attempt only; no automatic callback.\n\nApprove this plan for this call only. The existing schedule and paused/active state will not change.',
        confirmLabel: action == 'activate'
            ? 'Confirm activation'
            : action == 'finish'
            ? 'Finish plan'
            : 'Approve and call once',
      );
      if (!mounted) return;
      setState(() => _confirming = false);
      if (confirmed != true) return;
    }
    setState(() => _confirming = false);
    final id = approvedPlan.id;
    final random = Random.secure();
    final dispatch = await widget.api.action(
      id,
      action,
      body: action == 'call'
          ? {
              'approved': true,
              'revision': approvedPlan.data['revision'],
              'expected_phone': destination,
              'request_key': List.generate(
                24,
                (_) => random.nextInt(256).toRadixString(16).padLeft(2, '0'),
              ).join(),
            }
          : null,
    );
    final plan = await widget.api.plan(id);
    if (!mounted) return;
    setState(() {
      _selected = plan;
      _tab = plan.status == 'finished'
          ? 'Finished'
          : plan.needsReview
          ? 'Needs review'
          : 'Active';
      // Do not claim a call was dispatched when the offline implementation did nothing.
      if (action != 'call') {
        _notice = 'Plan updated.';
      } else if (widget.sample) {
        _notice = null;
      } else if (dispatch?['mock'] == true) {
        _notice = 'Simulated call only. No phone call was placed.';
      } else if (dispatch?['status'] == 'in_progress') {
        _notice = 'Call initiated. Refresh for results.';
      } else {
        _notice =
            'Call status: ${dispatch?['status'] ?? 'unconfirmed'}. Refresh history before retrying.';
      }
    });
    await _load();
  }

  Future<void> _edit({bool create = false}) async {
    if (_locked) return;
    setState(() => _busy = true);
    final result = await Navigator.of(context).push<FollowUpPlan>(
      MaterialPageRoute(
        builder: (_) => PlanEditor(
          api: widget.api,
          doctorId: widget.doctorId,
          plan: create ? null : _selected,
          sample: widget.sample,
        ),
      ),
    );
    if (!mounted) return;
    setState(() {
      _busy = false;
      if (result != null) {
        _selected = result;
        _tab = result.needsReview
            ? 'Needs review'
            : result.status == 'finished'
            ? 'Finished'
            : 'Active';
      }
    });
    if (result != null) await _load();
  }

  Widget _brand() => const Row(
    children: [
      Icon(FLucideIcons.phone, color: teal, size: 22),
      SizedBox(width: 10),
      Text(
        'medicall',
        style: TextStyle(fontSize: 23, color: ink, fontWeight: FontWeight.w700),
      ),
    ],
  );

  Widget _sidebar() => Container(
    width: 176,
    decoration: const BoxDecoration(
      color: Colors.white,
      border: Border(right: BorderSide(color: Color(0xFFDCE8E3))),
    ),
    padding: const EdgeInsets.fromLTRB(16, 22, 16, 16),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _brand(),
        const SizedBox(height: 32),
        const Text(
          'FOLLOW-UP PLANS',
          style: TextStyle(
            fontSize: 10,
            color: Color(0xFF62766F),
            letterSpacing: 1.2,
            fontWeight: FontWeight.w600,
          ),
        ),
        const SizedBox(height: 12),
        for (final (tab, icon) in [
          ('Active', FLucideIcons.phone),
          ('Needs review', FLucideIcons.clipboardCheck),
          ('Finished', FLucideIcons.check),
        ])
          Padding(
            padding: const EdgeInsets.only(bottom: 6),
            child: SizedBox(
              width: 144,
              child: FButton(
                size: FButtonSizeVariant.sm,
                variant: _tab == tab
                    ? FButtonVariant.secondary
                    : FButtonVariant.ghost,
                onPress: _locked ? null : () => _switchTab(tab),
                mainAxisAlignment: MainAxisAlignment.start,
                child: SizedBox(
                  width: 116,
                  child: Row(
                    children: [
                      Icon(icon, size: 15),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(tab, style: const TextStyle(fontSize: 12)),
                      ),
                      const SizedBox(width: 6),
                      Text(
                        '${_plans.where((p) => _matches(p, tab)).length}',
                        style: const TextStyle(fontSize: 12),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        const Spacer(),
        if (widget.onDisconnect != null)
          WorkspaceButton(
            onPress: _locked ? null : widget.onDisconnect,
            variant: FButtonVariant.ghost,
            prefix: const Icon(FLucideIcons.logOut, size: 15),
            child: const Text('Disconnect'),
          ),
      ],
    ),
  );

  Widget _searchField() => FTextField(
    hint: 'Search patients or instructions',
    size: FTextFieldSizeVariant.sm,
    prefixBuilder: (context, style, variants) => const Padding(
      padding: EdgeInsets.only(left: 10),
      child: Icon(FLucideIcons.search, size: 15),
    ),
    control: FTextFieldControl.managed(
      onChange: (v) => setState(() => _search = v.text),
    ),
  );

  Widget _actions() => Row(
    mainAxisSize: MainAxisSize.min,
    children: [
      WorkspaceButton(
        onPress: _locked ? null : _refresh,
        variant: FButtonVariant.outline,
        prefix: const Icon(FLucideIcons.refreshCw, size: 14),
        child: const Text('Refresh'),
      ),
      const SizedBox(width: 8),
      WorkspaceButton(
        onPress: _locked ? null : () => _edit(create: true),
        prefix: const Icon(FLucideIcons.plus, size: 14),
        child: const Text('New plan'),
      ),
    ],
  );

  Widget _toolbar(bool desktop) => Container(
    color: Colors.white,
    padding: EdgeInsets.symmetric(horizontal: desktop ? 22 : 16, vertical: 14),
    child: desktop
        ? Row(
            children: [
              const Text(
                'Follow-up plans',
                style: TextStyle(fontSize: 21, fontWeight: FontWeight.w700),
              ),
              const SizedBox(width: 12),
              Text(
                '${_plans.length} plans',
                style: const TextStyle(fontSize: 12, color: Color(0xFF62766F)),
              ),
              const Spacer(),
              SizedBox(
                width: MediaQuery.sizeOf(context).width >= 1120 ? 265 : 180,
                child: _searchField(),
              ),
              const SizedBox(width: 16),
              _actions(),
            ],
          )
        : Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  Expanded(child: _brand()),
                  if (widget.onDisconnect != null)
                    WorkspaceButton(
                      onPress: _locked ? null : widget.onDisconnect,
                      variant: FButtonVariant.ghost,
                      child: const Text('Disconnect'),
                    ),
                ],
              ),
              const SizedBox(height: 16),
              Row(
                children: [
                  const Expanded(
                    child: Text(
                      'Follow-up plans',
                      style: TextStyle(
                        fontSize: 18,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ),
                  _actions(),
                ],
              ),
              const SizedBox(height: 12),
              _searchField(),
              const SizedBox(height: 12),
              SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                child: Row(
                  children: [
                    for (final tab in ['Active', 'Needs review', 'Finished'])
                      Padding(
                        padding: const EdgeInsets.only(right: 6),
                        child: WorkspaceButton(
                          onPress: _locked ? null : () => _switchTab(tab),
                          variant: _tab == tab
                              ? FButtonVariant.secondary
                              : FButtonVariant.ghost,
                          child: Text(tab),
                        ),
                      ),
                  ],
                ),
              ),
            ],
          ),
  );

  Widget _list() => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 10),
        child: Row(
          children: [
            Expanded(
              child: Text(
                _tab,
                style: const TextStyle(fontWeight: FontWeight.w600),
              ),
            ),
            Text('${_visible.length}', style: const TextStyle(fontSize: 12)),
          ],
        ),
      ),
      if (_loading)
        const Padding(
          padding: EdgeInsets.all(20),
          child: Center(child: FCircularProgress()),
        )
      else if (_visible.isEmpty)
        Padding(
          padding: const EdgeInsets.all(16),
          child: Text(
            _plans.isEmpty
                ? 'No plans yet. Create a draft to get started.'
                : 'No matching plans.',
          ),
        ),
      for (final p in _visible)
        Semantics(
          selected: p.id == _selected?.id,
          child: FTappable(
            onPress: _locked ? null : () => _select(p),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
              decoration: BoxDecoration(
                color: p.id == _selected?.id ? mint : Colors.white,
                border: Border(
                  left: BorderSide(
                    color: p.id == _selected?.id ? teal : Colors.transparent,
                    width: 3,
                  ),
                  bottom: const BorderSide(color: Color(0xFFE4ECE8)),
                ),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Text(
                          p.name,
                          style: const TextStyle(
                            fontWeight: FontWeight.w600,
                            fontSize: 14,
                          ),
                        ),
                      ),
                      const SizedBox(width: 8),
                      Text(
                        p.status,
                        style: const TextStyle(fontSize: 11, color: teal),
                      ),
                    ],
                  ),
                  const SizedBox(height: 5),
                  Text(
                    p.instructions,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontSize: 12, height: 1.4),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    p.data['next_call_at'] == null
                        ? 'Next call not scheduled'
                        : utcTimestamp(p.data['next_call_at']),
                    style: const TextStyle(
                      fontSize: 11,
                      color: Color(0xFF62766F),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
    ],
  );

  Widget _detail() => _selected == null
      ? const Padding(
          padding: EdgeInsets.all(24),
          child: Text('Select a plan to review instructions and results.'),
        )
      : PlanDetail(
          key: ValueKey(_selected!.id),
          api: widget.api,
          plan: _selected!,
          busy: _locked,
          onAction: _action,
          onEdit: () => _edit(),
        );

  Widget _feedback() => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      if (_error != null)
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 10, 16, 0),
          child: FAlert(
            title: const Text('Unable to complete request'),
            subtitle: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(_error!),
                const SizedBox(height: 8),
                WorkspaceButton(
                  onPress: _locked ? null : _refresh,
                  variant: FButtonVariant.outline,
                  child: const Text('Retry / refresh'),
                ),
              ],
            ),
          ),
        ),
      if (_notice != null)
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 8),
          child: Text(_notice!, style: const TextStyle(fontSize: 12)),
        ),
      if (_busy && !_confirming) const FProgress(),
    ],
  );

  @override
  Widget build(BuildContext context) => Scaffold(
    body: SafeArea(
      child: LayoutBuilder(
        builder: (context, constraints) {
          final desktop = constraints.maxWidth >= 900;
          if (!desktop) {
            return Column(
              children: [
                _toolbar(false),
                _feedback(),
                Expanded(
                  child: SingleChildScrollView(
                    controller: _mobileScroll,
                    child: Column(
                      children: [
                        _list(),
                        Padding(
                          padding: const EdgeInsets.all(12),
                          child: _detail(),
                        ),
                      ],
                    ),
                  ),
                ),
              ],
            );
          }
          return Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              _sidebar(),
              Expanded(
                child: Column(
                  children: [
                    _toolbar(true),
                    _feedback(),
                    Expanded(
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Container(
                            width: 272,
                            decoration: const BoxDecoration(
                              color: Colors.white,
                              border: Border(
                                right: BorderSide(color: Color(0xFFDCE8E3)),
                              ),
                            ),
                            child: SingleChildScrollView(
                              controller: _masterScroll,
                              child: _list(),
                            ),
                          ),
                          Expanded(
                            child: SingleChildScrollView(
                              controller: _detailScroll,
                              padding: const EdgeInsets.all(18),
                              child: _detail(),
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
}
