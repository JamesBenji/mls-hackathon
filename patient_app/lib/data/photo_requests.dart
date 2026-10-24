import 'package:flutter/foundation.dart';

@immutable
class PhotoRequest {
  const PhotoRequest({
    required this.id,
    required this.title,
    required this.instructions,
    this.doctor = 'Dr. Sarah Mitchell',
  });

  final String id;
  final String title;
  final String instructions;
  final String doctor;
}

const demoPhotoRequest = PhotoRequest(
  id: 'demo-photo-request',
  title: 'Wound photo',
  instructions:
      'Take a clear photo of your wound. Keep dressings on unless your doctor told you to remove them.',
);

/// Local-only demo state. Replace with a backend repository when available.
class DemoCareStore extends ChangeNotifier {
  bool _submitted = false;

  bool get submitted => _submitted;

  void submit() {
    _submitted = true;
    notifyListeners();
  }

  void reset() {
    _submitted = false;
    notifyListeners();
  }
}
