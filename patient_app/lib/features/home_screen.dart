import 'dart:async';

import 'package:flutter/material.dart';
import 'package:forui/forui.dart';

import '../data/photo_requests.dart';
import '../notifications/push_notification_service.dart';
import '../ui/care_widgets.dart';
import 'photo_request_screen.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({this.pushNotifications, super.key});
  final PushNotificationService? pushNotifications;
  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> with WidgetsBindingObserver {
  final _api = CareApi();
  final _server = TextEditingController(text: CareApi.defaultBaseUrl);
  final _pairing = TextEditingController();
  final List<StreamSubscription<String>> _subscriptions = [];
  bool _busy = false;
  bool _requestOpen = false;
  String? _error;
  String? _pendingId;
  int _tab = 0;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _api.addListener(_apiChanged);
    final push = widget.pushNotifications;
    if (push != null) {
      push.registerToken = (token) async {
        if (_api.needsPairing) {
          throw const CareApiException('Pair this phone first.');
        }
        await _api.registerDevice(token);
      };
      _subscriptions.add(
        push.openedRequests.listen((id) {
          _pendingId = id;
          unawaited(_openPending());
        }),
      );
      _subscriptions.add(
        push.foregroundRequests.listen((id) {
          if (!mounted) return;
          setState(() => _pendingId = id);
          unawaited(_refresh());
        }),
      );
    }
    unawaited(_start());
  }

  void _apiChanged() {
    if (mounted) setState(() {});
  }

  Future<void> _start() async {
    await _api.initialize();
    if (!mounted) return;
    _server.text = _api.baseUrl;
    await widget.pushNotifications?.initialize();
    await _openPending();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed &&
        _api.ready &&
        !_api.needsPairing) {
      unawaited(_refresh());
      unawaited(widget.pushNotifications?.syncToken());
    }
  }

  Future<void> _refresh() async {
    if (_api.needsPairing || _busy) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await _api.refresh();
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
      await _api.pair(_server.text, _pairing.text);
      _pairing.clear();
      await widget.pushNotifications?.syncToken();
    } catch (e) {
      if (mounted) {
        setState(
          () => _error = e is CareApiException
              ? e.message
              : 'Could not securely save the connection. Please retry.',
        );
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
    await _openPending();
  }

  Future<void> _openPending() async {
    if (!mounted ||
        !_api.ready ||
        _api.needsPairing ||
        _pendingId == null ||
        _requestOpen) {
      return;
    }
    final id = _pendingId!;
    _pendingId = null;
    await _openRequest(id);
  }

  Future<void> _openRequest(String id) async {
    if (_requestOpen) {
      _pendingId = id;
      return;
    }
    _requestOpen = true;
    try {
      final request = await _api.request(id);
      if (!mounted) return;
      await Navigator.of(context).push<void>(
        MaterialPageRoute(
          builder: (_) => PhotoRequestScreen(request: request, api: _api),
        ),
      );
      await _refresh();
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    } finally {
      _requestOpen = false;
    }
    await _openPending();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _api.removeListener(_apiChanged);
    _api.dispose();
    _server.dispose();
    _pairing.dispose();
    for (final subscription in _subscriptions) {
      unawaited(subscription.cancel());
    }
    widget.pushNotifications?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => CarePage(
    bottom: _api.needsPairing ? null : _navigation(),
    child: SingleChildScrollView(
      padding: const EdgeInsets.all(24),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                padding: const EdgeInsets.all(9),
                decoration: BoxDecoration(
                  color: CareColors.teal,
                  borderRadius: BorderRadius.circular(13),
                ),
                child: const Icon(
                  Icons.add_rounded,
                  color: Colors.white,
                  size: 25,
                ),
              ),
              const SizedBox(width: 10),
              const Text(
                'medicall',
                style: TextStyle(
                  fontSize: 25,
                  fontWeight: FontWeight.w700,
                  letterSpacing: -1,
                ),
              ),
            ],
          ),
          const SizedBox(height: 30),
          if (!_api.ready)
            const FCircularProgress()
          else if (_api.needsPairing)
            ..._pairingContent()
          else ...[
            Text(
              _tab == 2
                  ? 'Your connection'
                  : _tab == 1
                  ? 'Your updates'
                  : 'Hi ${_api.patientName ?? ''}',
              style: const TextStyle(
                fontSize: 32,
                fontWeight: FontWeight.w600,
                letterSpacing: -1,
              ),
            ),
            const SizedBox(height: 12),
            const Text('Your care, connected.', style: bodyStyle),
            const SizedBox(height: 24),
            if (_pendingId != null) ...[
              FButton(
                onPress: _openPending,
                child: const Flexible(
                  child: Text(
                    'Open new photo request',
                    textAlign: TextAlign.center,
                  ),
                ),
              ),
              const SizedBox(height: 16),
            ],
            if (_tab == 2) ..._profile() else ..._requests(),
            const SizedBox(height: 20),
            FButton(
              variant: FButtonVariant.outline,
              onPress: _busy ? null : _refresh,
              prefix: _busy
                  ? const FCircularProgress()
                  : const Icon(Icons.refresh),
              child: Flexible(
                child: Text(
                  _busy ? 'Refreshing…' : 'Refresh requests',
                  textAlign: TextAlign.center,
                ),
              ),
            ),
          ],
          if (_error != null || _api.error != null) ...[
            const SizedBox(height: 16),
            Semantics(
              liveRegion: true,
              child: Text(
                _error ?? _api.error!,
                style: const TextStyle(color: Color(0xFFB64236), height: 1.5),
              ),
            ),
          ],
          const SizedBox(height: 28),
          const Text(
            'Not for emergencies. For urgent help, call local emergency services.',
            style: bodyStyle,
          ),
        ],
      ),
    ),
  );

  List<Widget> _pairingContent() => [
    const Text(
      'Connect to your care',
      style: TextStyle(
        fontSize: 32,
        fontWeight: FontWeight.w600,
        letterSpacing: -1,
      ),
    ),
    const SizedBox(height: 12),
    const Text(
      'Enter the patient pairing token shared privately by your care team. This connects only your patient account.',
      style: bodyStyle,
    ),
    const SizedBox(height: 24),
    FTextField(
      control: FTextFieldControl.managed(controller: _pairing),
      label: const Text('Patient pairing token'),
      obscureText: true,
      autocorrect: false,
      enableSuggestions: false,
      enableIMEPersonalizedLearning: false,
    ),
    const SizedBox(height: 16),
    FTextField(
      control: FTextFieldControl.managed(controller: _server),
      label: const Text('Care server'),
      autocorrect: false,
      keyboardType: TextInputType.url,
    ),
    const SizedBox(height: 20),
    FButton(
      onPress: _busy ? null : _pair,
      prefix: _busy ? const FCircularProgress() : const Icon(Icons.link),
      child: Flexible(
        child: Text(
          _busy ? 'Connecting…' : 'Connect securely',
          textAlign: TextAlign.center,
        ),
      ),
    ),
  ];

  List<Widget> _requests() {
    final requests = _tab == 0
        ? _api.requests.where((r) => !r.completed).toList()
        : _api.requests;
    return [
      SectionHeading(
        title: _tab == 0 ? 'To do' : 'Photo requests',
        trailing: StatusPill(
          label: '${requests.length} requests',
          pending: requests.isNotEmpty,
        ),
      ),
      const SizedBox(height: 16),
      if (requests.isEmpty)
        const CarePanel(
          child: Text(
            'No photo requests here. Refresh any time to check for updates.',
            style: bodyStyle,
          ),
        ),
      for (final request in requests) ...[
        CarePanel(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  const IconTile(icon: Icons.add_a_photo_outlined),
                  const Spacer(),
                  Flexible(
                    child: StatusPill(
                      label: request.completed
                          ? 'Photo received'
                          : 'Photo requested',
                      pending: !request.completed,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 16),
              Text(request.instructions, style: bodyStyle),
              if (request.completed) ...[
                const SizedBox(height: 12),
                Text(request.qualityLabel, style: bodyStyle),
              ],
              const SizedBox(height: 20),
              FButton(
                onPress: () => _openRequest(request.id),
                child: Flexible(
                  child: Text(
                    request.completed ? 'View details' : 'Add photo',
                    textAlign: TextAlign.center,
                  ),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 16),
      ],
    ];
  }

  List<Widget> _profile() => [
    CarePanel(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            _api.patientName ?? 'Patient',
            style: const TextStyle(fontSize: 24, fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 12),
          Text(_api.baseUrl, style: bodyStyle),
          if (_api.expiresAt != null)
            Text('Connection expires: ${_api.expiresAt} UTC', style: bodyStyle),
        ],
      ),
    ),
    const SizedBox(height: 16),
    if (widget.pushNotifications case final push?)
      ListenableBuilder(
        listenable: push,
        builder: (_, _) => CarePanel(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SectionHeading(title: 'Notifications'),
              const SizedBox(height: 12),
              Text(
                push.registered
                    ? 'This phone is registered for care notifications.'
                    : 'Enable notifications so new requests can reach this phone. You can always refresh requests manually.',
                style: bodyStyle,
              ),
              const SizedBox(height: 16),
              FButton(
                onPress: push.busy ? null : push.enableNotifications,
                child: Flexible(
                  child: Text(
                    push.busy
                        ? 'Connecting…'
                        : 'Enable / reconnect notifications',
                    textAlign: TextAlign.center,
                  ),
                ),
              ),
              if (push.error != null) ...[
                const SizedBox(height: 12),
                Text(push.error!, style: bodyStyle),
              ],
            ],
          ),
        ),
      ),
    const SizedBox(height: 16),
    FButton(
      variant: FButtonVariant.outline,
      onPress: _busy
          ? null
          : () async {
              setState(() => _busy = true);
              try {
                await _api.disconnect();
              } catch (e) {
                if (mounted) setState(() => _error = '$e');
              } finally {
                if (mounted) setState(() => _busy = false);
              }
            },
      child: const Flexible(
        child: Text('Disconnect this phone', textAlign: TextAlign.center),
      ),
    ),
  ];

  Widget _navigation() => Padding(
    padding: const EdgeInsets.all(12),
    child: Row(
      children: [
        for (final (index, label) in ['Home', 'Updates', 'You'].indexed)
          Expanded(
            child: FButton(
              variant: FButtonVariant.ghost,
              selected: _tab == index,
              onPress: () => setState(() => _tab = index),
              child: Flexible(child: Text(label, textAlign: TextAlign.center)),
            ),
          ),
      ],
    ),
  );
}
