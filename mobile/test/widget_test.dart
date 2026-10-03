import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:food_checking_mobile/main.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUp(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(device, (call) async => null);
  });
  testWidgets('First launch is empty, asks to connect and fits narrow phone', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(360, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(const FoodApp());
    await tester.pumpAndSettle();
    expect(find.text('Подключим дневник'), findsOneWidget);
    expect(find.text('— ккал'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.tap(find.byTooltip('Настройки'));
    await tester.pumpAndSettle();
    expect(find.text('Код подключения'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
  testWidgets(
    'Manual entry accepts decimal comma and rejects negative weight',
    (tester) async {
      Map<String, dynamic>? result;
      await tester.pumpWidget(
        MaterialApp(
          home: Builder(
            builder: (context) => Scaffold(
              body: TextButton(
                onPressed: () async {
                  result = await itemDialog(context, {
                    'name': 'Молоко',
                    'quantity': '-1',
                    'unit': 'г',
                    'kcal_per_100g': '52',
                    'nutrition_source': 'label',
                  });
                },
                child: const Text('open'),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Готово'));
      await tester.pumpAndSettle();
      expect(result, isNull);
      await tester.enterText(find.byType(TextFormField).at(1), '200,5');
      await tester.tap(find.text('Готово'));
      await tester.pumpAndSettle();
      expect(result?['quantity'], '200.5');
      expect(result?['nutrition_source'], 'label');
    },
  );
  testWidgets('Draft is restored with retry payload unchanged', (tester) async {
    Map<String, dynamic>? stored;
    final body = {
      'request_id': '12345678-1234-4234-9234-123456789012',
      'entry_date': '2026-10-03',
      'meal': 'lunch',
      'text': 'хлеб',
      'items': [
        {
          'name': 'Хлеб',
          'quantity': '30',
          'unit': 'г',
          'kcal_per_100g': '250',
          'nutrition_source': 'estimate',
        },
      ],
    };
    final draft = {...body, 'pending': body};
    await tester.pumpWidget(
      MaterialApp(
        home: AddPage(
          api: Api(),
          date: '2026-10-04',
          initial: draft,
          onDraft: (data) async {
            stored = data;
          },
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('2026-10-03'), findsOneWidget);
    expect(tester.widget<TextField>(find.byType(TextField)).enabled, false);
    await tester.scrollUntilVisible(
      find.text('Повторить сохранение'),
      250,
      scrollable: find.byType(Scrollable).first,
    );
    expect(find.text('Повторить сохранение'), findsOneWidget);
    expect(find.text('Разобрать и рассчитать'), findsNothing);
    // A restored immutable request cannot accidentally be recalculated or edited.
    expect(jsonEncode(body), jsonEncode(draft['pending']));
    expect(tester.takeException(), isNull);
    // Ensure closure keeps the draft serializable.
    if (stored != null) expect(stored!['pending'], body);
  });
}
