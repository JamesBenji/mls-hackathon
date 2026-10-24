import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:medicall/app.dart';

void main() {
  testWidgets('Home displays a pending photo request', (tester) async {
    await tester.pumpWidget(const MedicallApp());
    expect(find.text('medicall'), findsOneWidget);
    expect(find.text('Wound photo'), findsOneWidget);
    expect(find.text('1 to do'), findsOneWidget);
  });

  testWidgets('Photo request opens without a selected photo', (tester) async {
    await tester.pumpWidget(const MedicallApp());
    await tester.ensureVisible(find.text('Add photo'));
    await tester.tap(find.text('Add photo'));
    await tester.pumpAndSettle();
    expect(find.text('Instructions'), findsOneWidget);
    expect(find.text('Choose photo'), findsOneWidget);
    expect(find.text('Send photo'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('Demo notification opens the request', (tester) async {
    await tester.pumpWidget(const MedicallApp());
    await tester.ensureVisible(find.text('Test notification'));
    await tester.tap(find.text('Test notification'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Open request'));
    await tester.tap(find.text('Open request'));
    await tester.pumpAndSettle();
    expect(find.text('Photo request'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('Narrow layout remains usable', (tester) async {
    tester.view.physicalSize = const Size(320, 740);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(const MedicallApp());
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  });
}
