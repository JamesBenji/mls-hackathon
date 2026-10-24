import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:forui/forui.dart';
import 'package:image_picker/image_picker.dart';

import '../data/photo_requests.dart';
import '../ui/care_widgets.dart';

class PhotoRequestScreen extends StatefulWidget {
  const PhotoRequestScreen({
    required this.request,
    required this.api,
    super.key,
  });

  final PhotoRequest request;
  final CareApi api;

  @override
  State<PhotoRequestScreen> createState() => _PhotoRequestScreenState();
}

class _PhotoRequestScreenState extends State<PhotoRequestScreen> {
  Uint8List? _photo;
  bool _picking = false;
  bool _sending = false;
  String? _error;
  late PhotoRequest _request;
  String? _uploadKey;
  Timer? _poll;
  int _checks = 0;
  bool _refreshing = false;

  @override
  void initState() {
    super.initState();
    _request = widget.request;
    _scheduleCheck();
    unawaited(_recoverPhoto());
  }

  Future<void> _recoverPhoto() async {
    if (kIsWeb || defaultTargetPlatform != TargetPlatform.android) return;
    try {
      final lost = await ImagePicker().retrieveLostData();
      if (lost.files case final files? when files.isNotEmpty) {
        final bytes = await files.first.readAsBytes();
        if (mounted &&
            bytes.length <= 8 * 1024 * 1024 &&
            (!_request.completed || _request.canRetake)) {
          setState(() {
            _photo = bytes;
            _uploadKey = CareApi.newKey();
          });
        }
      }
      if (lost.exception != null && mounted) {
        setState(
          () => _error =
              'The camera could not return your photo. Please choose it again.',
        );
      }
    } catch (_) {
      if (mounted) {
        setState(
          () => _error =
              'Could not recover the camera photo. Please choose it again.',
        );
      }
    }
  }

  void _scheduleCheck() {
    _poll?.cancel();
    if (!mounted) return;
    if (_request.quality == 'checking' && _checks < 15) {
      _poll = Timer(const Duration(seconds: 4), () {
        unawaited(_refresh());
      });
    }
  }

  Future<void> _refresh({bool manual = false}) async {
    if (_refreshing || _sending) return;
    if (manual) _checks = 0;
    setState(() {
      _refreshing = true;
      _error = null;
    });
    try {
      final request = await widget.api.request(_request.id);
      if (mounted) setState(() => _request = request);
      _checks++;
      _scheduleCheck();
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    } finally {
      if (mounted) setState(() => _refreshing = false);
    }
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  Future<void> _pickPhoto(ImageSource source) async {
    setState(() {
      _picking = true;
      _error = null;
    });
    try {
      final picked = await ImagePicker().pickImage(
        source: source,
        maxWidth: 1600,
        maxHeight: 1600,
        imageQuality: 85,
        requestFullMetadata: false,
      );
      if (picked == null) {
        return;
      }
      final bytes = await picked.readAsBytes();
      if (!mounted) {
        return;
      }
      if (bytes.length > 8 * 1024 * 1024) {
        setState(() => _error = 'Choose a photo under 8 MB.');
        return;
      }
      setState(() {
        _photo = bytes;
        _uploadKey = CareApi.newKey();
      });
    } on PlatformException {
      if (mounted) {
        setState(() => _error = 'Allow camera or photo access in Settings.');
      }
    } catch (_) {
      if (mounted) {
        setState(() => _error = 'Can’t open this photo. Choose another.');
      }
    } finally {
      if (mounted) {
        setState(() => _picking = false);
      }
    }
  }

  Future<void> _submit() async {
    if (_photo == null || _sending) {
      return;
    }
    setState(() {
      _sending = true;
      _error = null;
    });
    var accepted = false;
    try {
      _uploadKey ??= CareApi.newKey();
      await widget.api.upload(_request.id, _photo!, _uploadKey!);
      accepted = true;
      if (!mounted) return;
      setState(() {
        _request = PhotoRequest.fromJson({
          'id': _request.id,
          'instructions': _request.instructions,
          'status': 'completed',
          'quality': {'status': 'checking', 'technical_feedback': <String>[]},
        });
        _photo = null;
        _uploadKey = null;
      });
      // A successful upload response confirms persistence, not quality approval.
      final request = await widget.api.request(_request.id);
      if (!mounted) return;
      setState(() {
        _request = request;
        _photo = null;
        _uploadKey = null;
      });
      _checks = 0;
      _scheduleCheck();
    } catch (e) {
      if (mounted) {
        setState(
          () => _error = accepted
              ? 'Photo received, but its latest status could not be loaded. Refresh to check.'
              : '$e',
        );
      }
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return CarePage(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const PageBackHeader(title: 'Photo request'),
            const SizedBox(height: 32),
            if (widget.api.needsPairing) ...[
              const Text(
                'Your patient connection is no longer valid. Return home and ask your care team for a new pairing token.',
                style: bodyStyle,
              ),
              const SizedBox(height: 16),
              FButton(
                onPress: () => Navigator.of(context).pop(),
                child: const Flexible(
                  child: Text(
                    'Return to connection',
                    textAlign: TextAlign.center,
                  ),
                ),
              ),
            ] else ...[
              if (_request.completed) ...[
                _success(),
                const SizedBox(height: 24),
              ],
              if (!_request.completed || _request.canRetake)
                ..._requestContent(),
            ],
            if (_error != null && _request.completed && !_request.canRetake)
              Text(_error!, style: const TextStyle(color: Color(0xFFB64236))),
          ],
        ),
      ),
    );
  }

  List<Widget> _requestContent() {
    return [
      const IconTile(icon: Icons.add_a_photo_outlined, size: 64),
      const SizedBox(height: 24),
      Text(
        _request.canRetake ? 'Optional retake' : 'Photo for your care team',
        style: const TextStyle(
          fontSize: 32,
          height: 1.15,
          letterSpacing: -1,
          fontWeight: FontWeight.w600,
        ),
      ),
      const SizedBox(height: 14),
      const Text('From your care team', style: bodyStyle),
      const SizedBox(height: 24),
      CarePanel(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Instructions',
              style: TextStyle(fontWeight: FontWeight.w600, fontSize: 15),
            ),
            const SizedBox(height: 10),
            Text(_request.instructions, style: bodyStyle),
          ],
        ),
      ),
      const SizedBox(height: 24),
      const SectionHeading(title: 'Add photo'),
      const SizedBox(height: 8),
      const Text(
        'Use good lighting. Keep the photo in focus.',
        style: bodyStyle,
      ),
      const SizedBox(height: 16),
      if (_photo != null) _preview() else _emptyPhoto(),
      const SizedBox(height: 16),
      if (!kIsWeb) ...[
        FButton(
          variant: FButtonVariant.outline,
          onPress: _picking || _sending
              ? null
              : () => _pickPhoto(ImageSource.camera),
          prefix: const Icon(Icons.camera_alt_outlined),
          child: const Flexible(
            child: Text('Take photo', textAlign: TextAlign.center),
          ),
        ),
        const SizedBox(height: 10),
      ],
      FButton(
        variant: FButtonVariant.outline,
        onPress: _picking || _sending
            ? null
            : () => _pickPhoto(ImageSource.gallery),
        prefix: _picking
            ? const FCircularProgress()
            : const Icon(Icons.photo_library_outlined),
        child: Flexible(
          child: Text(
            _picking
                ? 'Opening photos…'
                : _photo == null
                ? 'Choose photo'
                : 'Change photo',
            textAlign: TextAlign.center,
          ),
        ),
      ),
      if (_error != null) ...[
        const SizedBox(height: 12),
        Semantics(
          liveRegion: true,
          child: Text(
            _error!,
            style: const TextStyle(color: Color(0xFFB64236), height: 1.5),
          ),
        ),
      ],
      const SizedBox(height: 28),
      FButton(
        onPress: _photo == null || _sending || _picking ? null : _submit,
        prefix: _sending
            ? const FCircularProgress()
            : const Icon(Icons.arrow_upward_rounded),
        child: Flexible(
          child: Text(
            _sending
                ? 'Uploading…'
                : _request.canRetake
                ? 'Send replacement photo'
                : 'Send photo',
            textAlign: TextAlign.center,
          ),
        ),
      ),
      const SizedBox(height: 24),
    ];
  }

  Widget _emptyPhoto() {
    return CarePanel(
      color: const Color(0xFFF0F4F2),
      child: const Padding(
        padding: EdgeInsets.symmetric(vertical: 28),
        child: Column(
          children: [
            Icon(Icons.image_outlined, color: CareColors.teal, size: 36),
            SizedBox(height: 14),
            Text(
              'No photo selected',
              style: TextStyle(fontWeight: FontWeight.w500),
            ),
            SizedBox(height: 6),
            Text('1 photo · Up to 8 MB', style: bodyStyle),
          ],
        ),
      ),
    );
  }

  Widget _preview() {
    return CarePanel(
      padding: const EdgeInsets.all(10),
      child: Column(
        children: [
          ClipRRect(
            borderRadius: BorderRadius.circular(17),
            child: Image.memory(
              _photo!,
              height: 250,
              width: double.infinity,
              fit: BoxFit.contain,
              semanticLabel: 'Selected photo preview',
              errorBuilder: (context, error, stackTrace) => const SizedBox(
                height: 150,
                child: Center(
                  child: Text('Can’t preview. Choose another photo.'),
                ),
              ),
            ),
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              const SizedBox(width: 8),
              const Expanded(
                child: Text(
                  '1 photo selected',
                  style: TextStyle(fontSize: 12, color: CareColors.muted),
                ),
              ),
              FButton.icon(
                variant: FButtonVariant.ghost,
                semanticsLabel: 'Remove photo',
                onPress: _sending || _picking
                    ? null
                    : () => setState(() => _photo = null),
                child: const Icon(Icons.delete_outline_rounded, size: 19),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _success() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SizedBox(height: 32),
        const IconTile(icon: Icons.check_rounded, size: 80),
        const SizedBox(height: 28),
        const StatusPill(label: 'Photo received'),
        const SizedBox(height: 18),
        Text(
          _request.qualityLabel,
          style: TextStyle(
            fontSize: 34,
            height: 1.2,
            letterSpacing: -1,
            fontWeight: FontWeight.w600,
          ),
        ),
        const SizedBox(height: 16),
        const Text(
          'Quality feedback is technical guidance only, not a medical assessment. Your uploaded photo remains available to your care team.',
          style: bodyStyle,
        ),
        if (_request.feedback.isNotEmpty) ...[
          const SizedBox(height: 12),
          Text(
            _request.feedback
                .map(
                  (code) => switch (code) {
                    'blur' => 'Keep the camera steady and focus on the area.',
                    'too_dark' => 'Use brighter, even lighting.',
                    'glare' => 'Avoid direct glare or reflections.',
                    'framing' => 'Keep the requested area in frame.',
                    'too_far' => 'Move a little closer while keeping focus.',
                    'obstructed' =>
                      'Keep the requested area visible; follow your care instructions.',
                    _ => 'Review the lighting and framing.',
                  },
                )
                .join('\n'),
            style: bodyStyle,
          ),
        ],
        if (_request.canRetake) ...[
          const SizedBox(height: 12),
          const Text(
            'A retake is optional. Keep the current photo by leaving this screen. It is replaced only after a new upload succeeds; previous versions are not kept.',
            style: bodyStyle,
          ),
        ],
        if (_request.quality == 'checking') ...[
          const SizedBox(height: 12),
          if (_checks < 15) const FCircularProgress(),
          Text(
            _checks < 15
                ? 'Checking automatically; you can leave any time…'
                : 'Still checking. You can leave and refresh later.',
            style: bodyStyle,
          ),
        ],
        const SizedBox(height: 16),
        FButton(
          variant: FButtonVariant.outline,
          onPress: _refreshing || _sending
              ? null
              : () => _refresh(manual: true),
          child: Flexible(
            child: Text(
              _refreshing ? 'Refreshing…' : 'Refresh quality status',
              textAlign: TextAlign.center,
            ),
          ),
        ),
        const SizedBox(height: 28),
        FButton(
          onPress: () => Navigator.of(context).pop(),
          suffix: const Icon(Icons.arrow_forward_rounded),
          child: const Flexible(
            child: Text('Back to home', textAlign: TextAlign.center),
          ),
        ),
      ],
    );
  }
}
