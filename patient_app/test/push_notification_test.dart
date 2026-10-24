import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:medicall/notifications/push_notification_service.dart';

void main() {
  test('Notifications without request IDs never invent a request', () {
    const message = RemoteMessage(
      notification: RemoteNotification(title: 'Test'),
    );
    expect(requestIdFromMessage(message), isNull);
  });

  test('Backend request ID is preserved, never replaced by the demo ID', () {
    const message = RemoteMessage(
      data: {'type': 'photo_request', 'requestId': 'another-request'},
    );
    expect(requestIdFromMessage(message), 'another-request');
  });

  test('Unrelated messages do not navigate', () {
    const message = RemoteMessage(
      data: {'type': 'appointment'},
      notification: RemoteNotification(title: 'Test'),
    );
    expect(requestIdFromMessage(message), isNull);
    expect(requestIdFromMessage(const RemoteMessage()), isNull);
  });
}
