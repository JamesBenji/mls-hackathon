import 'package:flutter/material.dart';
import 'package:forui/forui.dart';
import '../app.dart' show teal, ink;
import '../models/follow_up_plan.dart';
import '../services/plan_api.dart';
import '../widgets/clinical_widgets.dart';
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
  int _selectionVersion = 0;
  @override
  void initState() {
    super.initState();
    if (widget.sample) _tab = 'Needs review';
    _load();
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
    } catch (e) {
      if (mounted) {
        setState(() {
          _loading = false;
          _error = '$e';
        });
      }
    }
  }

  Future<void> _select(FollowUpPlan p) async {
    final version = ++_selectionVersion;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final plan = await widget.api.plan(p.id);
      if (mounted && version == _selectionVersion) {
        setState(() => _selected = plan);
      }
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _action(String action) async {
    if (_busy || _selected == null) return;
    if (['call', 'activate', 'finish'].contains(action)) {
      setState(() {
        _busy = true;
        _confirming = true;
      });
      final confirmed = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: Text(
            action == 'activate'
                ? 'Activate this plan?'
                : action == 'finish'
                ? 'Finish this plan?'
                : widget.sample
                ? 'Preview Call now?'
                : 'Call ${_selected!.name} now?',
          ),
          content: Text(
            action == 'activate'
                ? '${_selected!.name}\n${_selected!.instructions}\n\n${_selected!.schedule['frequency']} at ${_selected!.schedule['local_time']} · ${_selected!.schedule['timezone']}\n${_selected!.schedule['start_date']} → ${_selected!.schedule['end_date']}\n\n${widget.sample ? 'Sample only — no calls will be placed.' : 'Activation enables scheduled calls. Confirm the full plan is ready.'}'
                : action == 'finish'
                ? 'Stop future follow-up for ${_selected!.name}? This plan will move to Finished. A call already in progress is not ended.'
                : widget.sample
                ? 'No call will be placed in sample mode.'
                : 'This requests a real phone call immediately. Confirm the patient and phone number before continuing.',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel'),
            ),
            TextButton(
              onPressed: () => Navigator.pop(context, true),
              child: Text(
                action == 'activate'
                    ? 'Confirm activation'
                    : action == 'finish'
                    ? 'Finish plan'
                    : 'Confirm call',
              ),
            ),
          ],
        ),
      );
      if (!mounted) return;
      setState(() {
        _busy = false;
        _confirming = false;
      });
      if (confirmed != true) return;
    }
    setState(() {
      _busy = true;
      _error = null;
      _notice = null;
    });
    try {
      final id = _selected!.id;
      await widget.api.action(id, action);
      final plan = await widget.api.plan(id);
      if (!mounted) return;
      setState(() {
        _selected = plan;
        _tab = plan.status == 'finished'
            ? 'Finished'
            : plan.needsReview
            ? 'Needs review'
            : 'Active';
        _notice = action == 'call'
            ? (widget.sample
                  ? 'Preview only — no call placed.'
                  : 'Call requested. Refresh for results.')
            : 'Plan updated.';
      });
      await _load();
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _edit({bool create = false}) async {
    if (_busy) return;
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

  Widget _list() {
    final plans = _plans
        .where(
          (p) =>
              (_tab == 'Active'
                  ? p.status == 'active'
                  : _tab == 'Finished'
                  ? p.status == 'finished'
                  : p.needsReview) &&
              '${p.name} ${p.instructions}'.toLowerCase().contains(
                _search.toLowerCase(),
              ),
        )
        .toList();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        FTextField(
          hint: 'Search plans or patients',
          control: FTextFieldControl.managed(
            onChange: (v) => setState(() => _search = v.text),
          ),
        ),
        const SizedBox(height: 16),
        if (_loading)
          const Center(child: FCircularProgress())
        else if (plans.isEmpty)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 32),
            child: Text(
              _plans.isEmpty
                  ? 'No follow-up plans yet. Create a draft to get started.'
                  : 'No ${_tab.toLowerCase()} plans match.',
            ),
          ),
        for (final p in plans)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: FCard(
              child: InkWell(
                onTap: _busy ? null : () => _select(p),
                borderRadius: BorderRadius.circular(8),
                child: Container(
                  padding: const EdgeInsets.all(18),
                  decoration: BoxDecoration(
                    border: _selected?.id == p.id
                        ? const Border(left: BorderSide(color: teal, width: 3))
                        : null,
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
                                fontWeight: FontWeight.w700,
                                fontSize: 17,
                              ),
                            ),
                          ),
                          Text(p.status),
                        ],
                      ),
                      const SizedBox(height: 8),
                      Text(
                        p.instructions,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                      ),
                      const SizedBox(height: 12),
                      Text(
                        p.data['next_call_at'] == null
                            ? 'Next call: not scheduled'
                            : 'Next call: ${utcTimestamp(p.data['next_call_at'])}',
                      ),
                      if (p.data['last_summary'] != null)
                        Text(
                          '${p.data['last_summary']}',
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                        ),
                    ],
                  ),
                ),
              ),
            ),
          ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    body: SafeArea(
      child: LayoutBuilder(
        builder: (context, constraints) {
          final wide = constraints.maxWidth >= 1050;
          final detail = _selected == null
              ? const SectionCard(
                  title: 'A clear plan for every call',
                  child: Text(
                    'Select a plan to review instructions, timing and call results.',
                  ),
                )
              : PlanDetail(
                  key: ValueKey(_selected!.data),
                  api: widget.api,
                  plan: _selected!,
                  busy: _busy || _loading,
                  onAction: _action,
                  onEdit: () => _edit(),
                );
          return Column(
            children: [
              Container(
                color: Colors.white,
                padding: const EdgeInsets.symmetric(
                  horizontal: 24,
                  vertical: 16,
                ),
                child: Row(
                  children: [
                    const Icon(FLucideIcons.phone, color: teal),
                    const SizedBox(width: 12),
                    const Expanded(
                      child: Text(
                        'medicall',
                        style: TextStyle(
                          fontSize: 25,
                          fontWeight: FontWeight.w700,
                          color: ink,
                        ),
                      ),
                    ),
                    if (widget.sample)
                      const Flexible(
                        child: Text(
                          'Sample · no real calls',
                          style: TextStyle(color: teal),
                        ),
                      ),
                    if (widget.onDisconnect != null)
                      TextButton(
                        onPressed: _busy ? null : widget.onDisconnect,
                        child: const Text('Disconnect'),
                      ),
                  ],
                ),
              ),
              Expanded(
                child: SingleChildScrollView(
                  padding: EdgeInsets.all(wide ? 32 : 16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Wrap(
                        alignment: WrapAlignment.spaceBetween,
                        spacing: 16,
                        runSpacing: 16,
                        children: [
                          const Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                'Follow-up plans',
                                style: TextStyle(
                                  fontSize: 30,
                                  fontWeight: FontWeight.w700,
                                ),
                              ),
                              SizedBox(height: 6),
                              Text('Set the calls. Decide what to track.'),
                            ],
                          ),
                          Wrap(
                            spacing: 8,
                            children: [
                              ClinicalButton(
                                onPress: _busy || _loading
                                    ? null
                                    : () async {
                                        await _load();
                                        if (_selected != null) {
                                          await _select(_selected!);
                                        }
                                      },
                                variant: FButtonVariant.outline,
                                child: const Text('Refresh'),
                              ),
                              ClinicalButton(
                                onPress: _busy || _loading
                                    ? null
                                    : () => _edit(create: true),
                                child: const Text('New plan'),
                              ),
                            ],
                          ),
                        ],
                      ),
                      const SizedBox(height: 24),
                      Wrap(
                        spacing: 8,
                        runSpacing: 8,
                        children: [
                          for (final tab in [
                            'Active',
                            'Needs review',
                            'Finished',
                          ])
                            ClinicalButton(
                              onPress: () => setState(() => _tab = tab),
                              variant: _tab == tab
                                  ? FButtonVariant.secondary
                                  : FButtonVariant.ghost,
                              child: Text(tab),
                            ),
                        ],
                      ),
                      if (_error != null)
                        Padding(
                          padding: const EdgeInsets.symmetric(vertical: 16),
                          child: FAlert(
                            title: const Text('Unable to complete request'),
                            subtitle: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(_error!),
                                const SizedBox(height: 8),
                                TextButton(
                                  onPressed: _busy || _loading
                                      ? null
                                      : () async {
                                          await _load();
                                          if (_selected != null) {
                                            await _select(_selected!);
                                          }
                                        },
                                  child: const Text('Retry / refresh'),
                                ),
                              ],
                            ),
                          ),
                        ),
                      if (_notice != null)
                        Padding(
                          padding: const EdgeInsets.symmetric(vertical: 12),
                          child: Text(_notice!),
                        ),
                      if (_busy && !_confirming)
                        const Padding(
                          padding: EdgeInsets.all(8),
                          child: FProgress(),
                        ),
                      const SizedBox(height: 16),
                      if (wide)
                        Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            SizedBox(width: 350, child: _list()),
                            const SizedBox(width: 24),
                            Expanded(child: detail),
                          ],
                        )
                      else ...[
                        _list(),
                        const SizedBox(height: 24),
                        detail,
                      ],
                    ],
                  ),
                ),
              ),
            ],
          );
        },
      ),
    ),
  );
}
