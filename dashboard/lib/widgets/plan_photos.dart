import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:forui/forui.dart';

import '../models/follow_up_plan.dart';
import '../services/plan_api.dart';
import 'workspace_widgets.dart';

class PlanPhotos extends StatelessWidget {
  const PlanPhotos({super.key, required this.plan, required this.api});
  final FollowUpPlan plan;
  final PlanApi api;

  @override
  Widget build(BuildContext context) {
    final requests = (plan.data['photo_requests'] as List? ?? []).map(
      (request) => Map<String, dynamic>.from(request as Map),
    );
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (requests.isEmpty)
          const Text('No photo requested for this plan yet.'),
        for (final request in requests) ...[
          Text(
            '${request['instructions']}',
            style: const TextStyle(fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 6),
          Text('Requested ${utcTimestamp(request['created_at'])}'),
          const SizedBox(height: 10),
          if (request['photo'] == null)
            Text(
              request['push_status'] == 'sent'
                  ? 'Waiting for upload · notification sent'
                  : 'Waiting for upload · notification ${request['push_status']}',
            )
          else ...[
            _PhotoPreview(
              key: ValueKey(
                '${request['photo']['id']}:${request['photo']['version']}',
              ),
              api: api,
              planId: plan.id,
              photoId: request['photo']['id'] as String,
            ),
            const SizedBox(height: 10),
            Text(switch (request['quality']?['status']) {
              'checking' => 'Checking photo quality…',
              'clear_enough' => 'Image clear enough for review',
              'retake_recommended' => 'Optional retake recommended',
              'uncertain' => 'Image quality uncertain',
              'failed' => 'Quality check unavailable · original photo retained',
              _ => 'Photo received',
            }),
            if ((request['quality']?['technical_feedback'] as List? ?? [])
                .isNotEmpty)
              Text(
                (request['quality']['technical_feedback'] as List).join(' · '),
              ),
            const Text(
              'Technical image-quality assessment only; not a diagnosis or clinical approval.',
              style: TextStyle(fontSize: 11, color: Color(0xFF647875)),
            ),
          ],
          const SizedBox(height: 20),
        ],
      ],
    );
  }
}

class _PhotoPreview extends StatefulWidget {
  const _PhotoPreview({
    super.key,
    required this.api,
    required this.planId,
    required this.photoId,
  });
  final PlanApi api;
  final int planId;
  final String photoId;

  @override
  State<_PhotoPreview> createState() => _PhotoPreviewState();
}

class _PhotoPreviewState extends State<_PhotoPreview> {
  late Future<Uint8List> _bytes;

  @override
  void initState() {
    super.initState();
    _bytes = widget.api.photoBytes(widget.planId, widget.photoId);
  }

  @override
  Widget build(BuildContext context) => FutureBuilder<Uint8List>(
    future: _bytes,
    builder: (context, snapshot) {
      if (snapshot.hasError) {
        return WorkspaceButton(
          variant: FButtonVariant.outline,
          onPress: () => setState(
            () => _bytes = widget.api.photoBytes(widget.planId, widget.photoId),
          ),
          child: const Text('Photo unavailable · Retry'),
        );
      }
      if (!snapshot.hasData) return const FProgress();
      return Image.memory(
        snapshot.data!,
        height: 260,
        fit: BoxFit.contain,
        semanticLabel: 'Patient-submitted photo',
        errorBuilder: (_, _, _) => const Text('Cannot display this image.'),
      );
    },
  );
}
