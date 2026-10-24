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
    required this.store,
    super.key,
  });

  final PhotoRequest request;
  final DemoCareStore store;

  @override
  State<PhotoRequestScreen> createState() => _PhotoRequestScreenState();
}

class _PhotoRequestScreenState extends State<PhotoRequestScreen> {
  Uint8List? _photo;
  bool _picking = false;
  bool _sending = false;
  String? _error;

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
      if (bytes.length > 10 * 1024 * 1024) {
        setState(() => _error = 'Choose a photo under 10 MB.');
        return;
      }
      setState(() => _photo = bytes);
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
    setState(() => _sending = true);
    // Simulated latency only. No network request or persistent upload occurs.
    await Future<void>.delayed(const Duration(milliseconds: 1100));
    if (!mounted) {
      return;
    }
    widget.store.submit();
    setState(() {
      _sending = false;
      _photo = null;
    });
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
            if (widget.store.submitted) _success() else ..._requestContent(),
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
        widget.request.title,
        style: const TextStyle(
          fontSize: 32,
          height: 1.15,
          letterSpacing: -1,
          fontWeight: FontWeight.w600,
        ),
      ),
      const SizedBox(height: 14),
      Text('From ${widget.request.doctor}', style: bodyStyle),
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
            Text(widget.request.instructions, style: bodyStyle),
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
          child: const Text('Take photo'),
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
        child: Text(
          _picking
              ? 'Opening photos…'
              : _photo == null
              ? 'Choose photo'
              : 'Change photo',
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
        child: Text(_sending ? 'Finishing…' : 'Send photo'),
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
            Text('1 photo · Up to 10 MB', style: bodyStyle),
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
        const StatusPill(label: 'Complete'),
        const SizedBox(height: 18),
        const Text(
          'Done',
          style: TextStyle(
            fontSize: 34,
            height: 1.2,
            letterSpacing: -1,
            fontWeight: FontWeight.w600,
          ),
        ),
        const SizedBox(height: 28),
        FButton(
          onPress: () => Navigator.of(context).pop(),
          suffix: const Icon(Icons.arrow_forward_rounded),
          child: const Text('Back to home'),
        ),
      ],
    );
  }
}
