import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:medicall/app.dart';

void main() {
  setUp(() => FlutterSecureStorage.setMockInitialValues({}));

  testWidgets('Unpaired app offers secure connection, not sample requests', (
    tester,
  ) async {
    await tester.pumpWidget(const MedicallApp());
    await tester.pumpAndSettle();
    expect(find.text('medicall'), findsOneWidget);
    expect(find.text('Patient pairing token'), findsOneWidget);
    expect(find.text('Connect securely'), findsOneWidget);
    expect(find.text('Wound photo'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('Narrow pairing layout remains usable', (tester) async {
    tester.view.physicalSize = const Size(320, 740);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(const MedicallApp());
    await tester.pumpAndSettle();
  });
}
