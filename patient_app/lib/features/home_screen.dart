import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
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

class _HomeScreenState extends State<HomeScreen> {
  final _store = DemoCareStore();
  int _tab = 0;
  bool _notificationVisible = false;
  String _notificationRequestId = demoPhotoRequest.id;
  bool _requestOpen = false;
  final List<StreamSubscription<String>> _pushSubscriptions = [];

  @override
  void initState() {
    super.initState();
    final push = widget.pushNotifications;
    if (push != null) {
      _pushSubscriptions.add(push.openedRequests.listen(_openPushRequest));
      _pushSubscriptions.add(
        push.foregroundRequests.listen((id) {
          if (!mounted) {
            return;
          }
          setState(() {
            _notificationRequestId = id;
            _notificationVisible = true;
            _tab = 0;
          });
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(
              content: const Text('New photo request'),
              action: SnackBarAction(
                label: 'Open',
                onPressed: () => _openPushRequest(id),
              ),
            ),
          );
        }),
      );
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) {
          unawaited(push.initialize());
        }
      });
    }
  }

  void _openPushRequest(String id) {
    if (!mounted) {
      return;
    }
    if (id != demoPhotoRequest.id) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('Request unavailable.')));
      return;
    }
    _openRequest();
  }

  @override
  void dispose() {
    for (final subscription in _pushSubscriptions) {
      unawaited(subscription.cancel());
    }
    widget.pushNotifications?.dispose();
    _store.dispose();
    super.dispose();
  }

  Future<void> _openRequest() async {
    if (_requestOpen) {
      return;
    }
    _requestOpen = true;
    setState(() => _notificationVisible = false);
    await Navigator.of(context).push<void>(
      MaterialPageRoute(
        builder: (context) =>
            PhotoRequestScreen(request: demoPhotoRequest, store: _store),
      ),
    );
    _requestOpen = false;
  }

  void _demoNotification() {
    _store.reset();
    setState(() {
      _tab = 0;
      _notificationVisible = true;
      _notificationRequestId = demoPhotoRequest.id;
    });
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: _store,
      builder: (context, child) => CarePage(
        bottom: _navigation(),
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(24, 20, 24, 24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _header(),
              const SizedBox(height: 30),
              if (_notificationVisible) ...[
                _notification(),
                const SizedBox(height: 24),
              ],
              if (_tab == 0) ..._homeContent(),
              if (_tab == 1) ..._activityContent(),
              if (_tab == 2) ..._profileContent(),
              if (widget.pushNotifications != null) ...[
                const SizedBox(height: 24),
                _pushSettings(),
              ],
              const SizedBox(height: 28),
              FButton(
                variant: FButtonVariant.outline,
                onPress: _demoNotification,
                prefix: const Icon(Icons.notifications_active_outlined),
                child: const Flexible(
                  child: Text('Test notification', textAlign: TextAlign.center),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _pushSettings() {
    final push = widget.pushNotifications!;
    return ListenableBuilder(
      listenable: push,
      builder: (context, child) => CarePanel(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Notifications',
              style: TextStyle(fontSize: 17, fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 8),
            const Text('Enable notifications on this phone.', style: bodyStyle),
            const SizedBox(height: 16),
            FButton(
              onPress: push.busy ? null : push.enableNotifications,
              prefix: push.busy
                  ? const FCircularProgress()
                  : const Icon(Icons.notifications_outlined),
              child: Flexible(
                child: Text(
                  push.busy ? 'Connecting…' : 'Enable notifications',
                  textAlign: TextAlign.center,
                ),
              ),
            ),
            if (push.token != null) ...[
              const SizedBox(height: 12),
              FButton(
                variant: FButtonVariant.outline,
                onPress: () async {
                  await Clipboard.setData(ClipboardData(text: push.token!));
                  if (!context.mounted) {
                    return;
                  }
                  ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(content: Text('Token copied.')),
                  );
                },
                prefix: const Icon(Icons.copy_outlined),
                child: const Flexible(
                  child: Text('Copy FCM token', textAlign: TextAlign.center),
                ),
              ),
              const SizedBox(height: 12),
              SelectableText(
                push.token!,
                style: const TextStyle(fontSize: 10, color: CareColors.muted),
              ),
            ],
            if (push.error != null) ...[
              const SizedBox(height: 12),
              Semantics(
                liveRegion: true,
                child: Text(
                  push.error!,
                  style: bodyStyle.copyWith(color: const Color(0xFFB64236)),
                ),
              ),
            ],
            const SizedBox(height: 12),
            const Text(
              'To test a push, leave the app first.',
              style: TextStyle(
                fontSize: 11,
                height: 1.6,
                color: CareColors.muted,
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _header() {
    return Row(
      children: [
        Container(
          padding: const EdgeInsets.all(9),
          decoration: BoxDecoration(
            color: CareColors.teal,
            borderRadius: BorderRadius.circular(13),
          ),
          child: const Icon(Icons.add_rounded, color: Colors.white, size: 25),
        ),
        const SizedBox(width: 10),
        const Expanded(
          child: Text(
            'medicall',
            style: TextStyle(
              fontSize: 25,
              fontWeight: FontWeight.w700,
              letterSpacing: -1,
            ),
          ),
        ),
        FButton.icon(
          variant: FButtonVariant.ghost,
          semanticsLabel: 'View notifications',
          onPress: () => setState(() => _tab = 1),
          child: Badge(
            isLabelVisible: !_store.submitted,
            smallSize: 7,
            backgroundColor: CareColors.teal,
            child: const Icon(Icons.notifications_none_rounded),
          ),
        ),
      ],
    );
  }

  List<Widget> _homeContent() {
    return [
      const Text(
        'YOUR FOLLOW-UP',
        style: TextStyle(
          fontSize: 10,
          letterSpacing: 1.8,
          fontWeight: FontWeight.w600,
          color: CareColors.teal,
        ),
      ),
      const SizedBox(height: 10),
      const Text(
        'Hi Alex',
        style: TextStyle(
          fontSize: 38,
          height: 1.15,
          letterSpacing: -1.5,
          fontWeight: FontWeight.w600,
        ),
      ),
      const SizedBox(height: 12),
      const Text('Here’s what’s next.', style: bodyStyle),
      const SizedBox(height: 26),
      CarePanel(
        color: CareColors.mint,
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Icon(
              Icons.favorite_border_rounded,
              color: CareColors.teal,
              size: 27,
            ),
            const SizedBox(width: 16),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'Your doctor',
                    style: TextStyle(fontWeight: FontWeight.w600, fontSize: 15),
                  ),
                  const SizedBox(height: 6),
                  const Text(
                    'Follow-up care between visits.',
                    style: bodyStyle,
                  ),
                  const SizedBox(height: 14),
                  Row(
                    children: [
                      const CircleAvatar(
                        radius: 14,
                        backgroundColor: Colors.white,
                        child: Text(
                          'SM',
                          style: TextStyle(
                            fontSize: 9,
                            color: CareColors.teal,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          demoPhotoRequest.doctor,
                          style: const TextStyle(
                            fontSize: 12,
                            fontWeight: FontWeight.w500,
                          ),
                        ),
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
      const SizedBox(height: 28),
      SectionHeading(
        title: 'To do',
        trailing: StatusPill(
          label: _store.submitted ? 'Done' : '1 to do',
          pending: !_store.submitted,
        ),
      ),
      const SizedBox(height: 14),
      _requestCard(),
      const SizedBox(height: 28),
      const SectionHeading(title: 'Recent activity'),
      const SizedBox(height: 14),
      _journey(),
    ];
  }

  Widget _requestCard() {
    final submitted = _store.submitted;
    return CarePanel(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              IconTile(
                icon: submitted
                    ? Icons.check_rounded
                    : Icons.add_a_photo_outlined,
              ),
              const Spacer(),
              Flexible(
                child: StatusPill(
                  label: submitted ? 'Complete' : 'Photo requested',
                  pending: !submitted,
                ),
              ),
            ],
          ),
          const SizedBox(height: 18),
          Text(
            submitted ? 'Photo added' : demoPhotoRequest.title,
            style: const TextStyle(
              fontSize: 21,
              fontWeight: FontWeight.w600,
              letterSpacing: -0.5,
            ),
          ),
          const SizedBox(height: 8),
          Text(
            submitted
                ? 'Photo request completed.'
                : 'Dr. Mitchell requested a wound photo.',
            style: bodyStyle,
          ),
          const SizedBox(height: 20),
          FButton(
            onPress: _openRequest,
            suffix: Icon(
              submitted ? Icons.check_rounded : Icons.arrow_forward_rounded,
            ),
            child: Flexible(
              child: Text(
                submitted ? 'View details' : 'Add photo',
                textAlign: TextAlign.center,
              ),
            ),
          ),
          if (!submitted) ...[
            const SizedBox(height: 12),
            const Center(
              child: Text(
                'About 1 minute',
                style: TextStyle(fontSize: 11, color: CareColors.muted),
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _journey() {
    return CarePanel(
      child: Column(
        children: [
          _journeyRow(
            Icons.call_outlined,
            'Follow-up call',
            'Today · 9:30 AM',
            'Call completed.',
          ),
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 16),
            child: Divider(height: 1, color: CareColors.border),
          ),
          _journeyRow(
            _store.submitted
                ? Icons.check_circle_outline
                : Icons.photo_camera_outlined,
            _store.submitted ? 'Photo added' : 'Photo request',
            'Today · After your call',
            _store.submitted
                ? 'Photo request completed.'
                : 'Add a wound photo.',
          ),
        ],
      ),
    );
  }

  Widget _journeyRow(
    IconData icon,
    String title,
    String time,
    String description,
  ) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        IconTile(icon: icon, size: 38),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                time,
                style: const TextStyle(fontSize: 10, color: CareColors.muted),
              ),
              const SizedBox(height: 5),
              Text(
                title,
                style: const TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const SizedBox(height: 4),
              Text(description, style: bodyStyle.copyWith(fontSize: 12)),
            ],
          ),
        ),
      ],
    );
  }

  List<Widget> _activityContent() {
    return [
      const Text(
        'Updates',
        style: TextStyle(
          fontSize: 32,
          fontWeight: FontWeight.w600,
          letterSpacing: -1,
        ),
      ),
      const SizedBox(height: 10),
      const Text('Requests and recent activity.', style: bodyStyle),
      const SizedBox(height: 24),
      _requestCard(),
      const SizedBox(height: 24),
      _journey(),
    ];
  }

  List<Widget> _profileContent() {
    return [
      const Text(
        'Profile',
        style: TextStyle(
          fontSize: 32,
          fontWeight: FontWeight.w600,
          letterSpacing: -1,
        ),
      ),
      const SizedBox(height: 24),
      const CarePanel(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            IconTile(icon: Icons.person_outline_rounded),
            SizedBox(height: 16),
            Text(
              'Alex Morgan',
              style: TextStyle(fontSize: 24, fontWeight: FontWeight.w600),
            ),
            SizedBox(height: 6),
            SizedBox(height: 24),
            Divider(color: CareColors.border),
            SizedBox(height: 16),
            Text(
              'YOUR DOCTOR',
              style: TextStyle(
                fontSize: 10,
                letterSpacing: 1.5,
                color: CareColors.muted,
              ),
            ),
            SizedBox(height: 10),
            Text(
              'Dr. Sarah Mitchell',
              style: TextStyle(fontSize: 17, fontWeight: FontWeight.w600),
            ),
            SizedBox(height: 6),
            Text('Follow-up care team', style: bodyStyle),
          ],
        ),
      ),
      const SizedBox(height: 16),
      const CarePanel(
        color: CareColors.mint,
        child: Text(
          'Not for emergencies. For urgent help, call local emergency services.',
          style: bodyStyle,
        ),
      ),
    ];
  }

  Widget _notification() {
    return CarePanel(
      color: const Color(0xFFEDF5F2),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(
                Icons.notifications_active_outlined,
                size: 16,
                color: CareColors.teal,
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  'MEDICALL',
                  style: TextStyle(
                    fontSize: 10,
                    fontWeight: FontWeight.w600,
                    letterSpacing: 0.6,
                  ),
                ),
              ),
              FButton.icon(
                variant: FButtonVariant.ghost,
                semanticsLabel: 'Dismiss notification',
                onPress: () => setState(() => _notificationVisible = false),
                child: const Icon(Icons.close, size: 16),
              ),
            ],
          ),
          const SizedBox(height: 6),
          const Text(
            'New photo request',
            style: TextStyle(fontSize: 17, fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 8),
          const Text('Your doctor requested a photo.', style: bodyStyle),
          const SizedBox(height: 14),
          FButton(
            onPress: () => _openPushRequest(_notificationRequestId),
            child: const Text('Open request'),
          ),
        ],
      ),
    );
  }

  Widget _navigation() {
    const destinations = [
      (Icons.grid_view_rounded, 'Home'),
      (Icons.inbox_outlined, 'Updates'),
      (Icons.person_outline_rounded, 'You'),
    ];
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 12),
      decoration: const BoxDecoration(
        color: Colors.white,
        border: Border(top: BorderSide(color: CareColors.border)),
      ),
      child: Row(
        children: List.generate(destinations.length, (index) {
          final (icon, label) = destinations[index];
          return Expanded(
            child: FButton.raw(
              variant: FButtonVariant.ghost,
              selected: _tab == index,
              semanticsLabel: label,
              onPress: () => setState(() => _tab = index),
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 10),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                      icon,
                      size: 21,
                      color: _tab == index ? CareColors.teal : CareColors.muted,
                    ),
                    const SizedBox(height: 5),
                    Text(
                      label,
                      style: TextStyle(
                        fontSize: 10,
                        fontWeight: FontWeight.w600,
                        color: _tab == index
                            ? CareColors.teal
                            : CareColors.muted,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          );
        }),
      ),
    );
  }
}
