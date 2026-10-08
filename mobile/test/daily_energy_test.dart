import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:food_checking_mobile/main.dart';

import 'fake_network.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  testWidgets(
    'Training expenditure persists per date, refreshes delta, accepts zero and clears',
    (tester) async {
      final network = FakeNetwork();
      HttpOverrides.global = network;
      addTearDown(() => HttpOverrides.global = null);
      final spent = <String, dynamic>{};
      network.responder = (request) {
        if (request.uri.path.endsWith('/activity')) {
          final date = RegExp(r'/days/([^/]+)/activity$')
              .firstMatch(request.uri.path)!
              .group(1)!;
          spent[date] =
              (jsonDecode(utf8.decode(request.body)) as Map)['training_kcal'];
          return {'training_kcal': spent[date], 'entry_date': date};
        }
        if (request.uri.path.endsWith('/diary')) {
          final date = request.uri.queryParameters['entry_date']!;
          return {
            'entry_date': date,
            'total_kcal': '2100',
            'training_kcal': spent[date],
            'resting_kcal': '1867.5',
            'energy_mode': 'resting_training',
            'spent_kcal': spent[date] == null
                ? null
                : '${1867.5 + double.parse(spent[date])}',
            'energy_delta': spent[date] == null
                ? null
                : '${2100 - 1867.5 - double.parse(spent[date])}',
            'energy_delta_estimated': true,
            'energy_delta_incomplete': false,
            'items': [],
            'foods': [],
            'nutrition': [],
            'queued_count': 0,
          };
        }
        return null;
      };
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(
            device,
            (call) async => call.method == 'load'
                ? jsonEncode({
                    'url': publicApiBase,
                    'token': 'energy-test-token',
                  })
                : null,
          );
      Future<void> button(String text) async {
        await tester.scrollUntilVisible(
          find.text(text),
          150,
          scrollable: find.byType(Scrollable).first,
        );
        await tester.tap(find.text(text));
        await tester.pumpAndSettle();
      }

      final date = dayKey(DateTime.now());
      await tester.pumpWidget(const FoodApp());
      await tester.pumpAndSettle();
      expect(find.text('Потрачено: —'), findsOneWidget);
      expect(find.text('Разница: —'), findsOneWidget);
      await button('Ввести калории от тренировок');
      expect(find.text(date), findsOneWidget);
      await tester.enterText(find.byKey(const ValueKey('training_kcal')), '-1');
      await tester.tap(find.text('Сохранить'));
      await tester.pumpAndSettle();
      expect(network.requests.where((r) => r.method == 'PUT'), isEmpty);
      await tester.enterText(
        find.byKey(const ValueKey('training_kcal')),
        '500,50',
      );
      await tester.tap(find.text('Сохранить'));
      await tester.pumpAndSettle();
      expect(spent[date], '500.50');
      await tester.scrollUntilVisible(
        find.byType(DailyEnergy),
        -200,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.pumpAndSettle();
      expect(find.text('Разница: ≈ -268 ккал'), findsOneWidget);
      await tester.pumpWidget(const SizedBox.shrink());
      await tester.pumpAndSettle();
      await tester.pumpWidget(const FoodApp());
      await tester.pumpAndSettle();
      expect(find.text('Потрачено: 2368 ккал'), findsOneWidget);
      await tester.ensureVisible(find.byIcon(Icons.chevron_left));
      await tester.tap(find.byIcon(Icons.chevron_left));
      await tester.pumpAndSettle();
      expect(find.text('Потрачено: —'), findsOneWidget);
      await tester.tap(find.byIcon(Icons.chevron_right));
      await tester.pumpAndSettle();
      await button('Изменить калории от тренировок');
      await tester.enterText(find.byKey(const ValueKey('training_kcal')), '0');
      await tester.tap(find.text('Сохранить'));
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
        find.byType(DailyEnergy),
        -200,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.pumpAndSettle();
      expect(find.text('Разница: ≈ +232.5 ккал'), findsOneWidget);
      await button('Изменить калории от тренировок');
      await tester.tap(find.text('Убрать значение'));
      await tester.pumpAndSettle();
      expect(spent[date], isNull);
      await tester.scrollUntilVisible(
        find.byType(DailyEnergy),
        -200,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.pumpAndSettle();
      expect(find.text('Разница: —'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('Weight settings load saved profile, validate and save changes', (
    tester,
  ) async {
    final network = FakeNetwork();
    HttpOverrides.global = network;
    addTearDown(() => HttpOverrides.global = null);
    var profile = <String, dynamic>{
      'weight_kg': '96.00',
      'height_cm': '170.00',
      'age': 32,
      'name': 'Мой дневник',
      'sex': 'male',
      'birth_date': '1994-08-27',
      'resting_kcal': '1867.5',
    };
    network.responder = (request) {
      if (request.uri.path.endsWith('/energy/profile')) {
        if (request.method == 'PUT') {
          final body = jsonDecode(utf8.decode(request.body)) as Map;
          profile = {
            ...profile,
            ...Map<String, dynamic>.from(body),
            'resting_kcal': '1857.5',
          };
        }
        return profile;
      }
      return null;
    };
    final api = Api()..token = 'test-token';
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => Scaffold(
            body: TextButton(
              onPressed: () => energyProfileDialog(context, api),
              child: const Text('Профиль'),
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.text('Профиль'));
    await tester.pumpAndSettle();
    expect(find.text('Возраст: 32'), findsOneWidget);
    await tester.scrollUntilVisible(
      find.textContaining('1867.5'),
      160,
      scrollable: find.byType(Scrollable).last,
    );
    expect(find.textContaining('1867.5'), findsOneWidget);
    await tester.ensureVisible(find.byKey(const ValueKey('weight_kg')));
    await tester.enterText(find.byKey(const ValueKey('weight_kg')), '0');
    await tester.tap(find.text('Сохранить'));
    await tester.pumpAndSettle();
    expect(network.requests.where((r) => r.method == 'PUT'), isEmpty);
    await tester.ensureVisible(find.byKey(const ValueKey('weight_kg')));
    await tester.enterText(find.byKey(const ValueKey('weight_kg')), '95,00');
    await tester.tap(find.text('Сохранить'));
    await tester.pumpAndSettle();
    expect(profile['weight_kg'], '95.00');
    expect(profile['height_cm'], '170.00');
    await tester.tap(find.text('Профиль'));
    await tester.pumpAndSettle();
    final field = tester.widget<TextFormField>(
      find.byKey(const ValueKey('weight_kg')),
    );
    expect(field.controller!.text, '95.00');
    expect(tester.takeException(), isNull);
    await tester.tap(find.text('Закрыть'));
    await tester.pumpAndSettle();
  });

  testWidgets(
    'Second user fills personal demographics without owner defaults',
    (tester) async {
      final network = FakeNetwork();
      HttpOverrides.global = network;
      addTearDown(() => HttpOverrides.global = null);
      Map<String, dynamic>? saved;
      network.responder = (request) {
        if (!request.uri.path.endsWith('/energy/profile')) return null;
        if (request.method == 'PUT') {
          saved = Map<String, dynamic>.from(
            jsonDecode(utf8.decode(request.body)) as Map,
          );
          return saved!;
        }
        return {
          'name': 'Пользователь 2',
          'birth_date': null,
          'sex': null,
          'weight_kg': null,
          'height_cm': null,
          'age': null,
          'resting_kcal': null,
        };
      };
      final api = Api()..token = 'second-user-token';
      await tester.pumpWidget(
        MaterialApp(
          home: Builder(
            builder: (context) => Scaffold(
              body: TextButton(
                onPressed: () => energyProfileDialog(context, api),
                child: const Text('Профиль'),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.text('Профиль'));
      await tester.pumpAndSettle();
      expect(
        tester
            .widget<TextFormField>(find.byKey(const ValueKey('birth_date')))
            .controller!
            .text,
        '',
      );
      expect(
        tester
            .widget<TextFormField>(find.byKey(const ValueKey('height_cm')))
            .controller!
            .text,
        '',
      );
      await tester.tap(find.text('Сохранить'));
      await tester.pumpAndSettle();
      expect(saved, isNull);
      await tester.ensureVisible(find.byKey(const ValueKey('person_name')));
      await tester.enterText(find.byKey(const ValueKey('person_name')), 'Анна');
      await tester.ensureVisible(find.byKey(const ValueKey('birth_date')));
      await tester.tap(find.byKey(const ValueKey('birth_date')));
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('Switch to input'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField).last, '10/03/1996');
      await tester.tap(find.text('OK'));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.byKey(const ValueKey('person_sex')));
      await tester.tap(find.byKey(const ValueKey('person_sex')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Женский').last);
      await tester.pumpAndSettle();
      for (final input in {'weight_kg': '65', 'height_cm': '165'}.entries) {
        await tester.ensureVisible(find.byKey(ValueKey(input.key)));
        await tester.enterText(find.byKey(ValueKey(input.key)), input.value);
      }
      await tester.tap(find.text('Сохранить'));
      await tester.pumpAndSettle();
      expect(saved, {
        'name': 'Анна',
        'birth_date': '1996-10-03',
        'sex': 'female',
        'weight_kg': '65',
        'height_cm': '165',
      });
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'Incomplete and estimated energy remain readable at large font size',
    (tester) async {
      tester.view.physicalSize = const Size(320, 800);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      await tester.pumpWidget(
        MaterialApp(
          home: MediaQuery(
            data: const MediaQueryData(textScaler: TextScaler.linear(2)),
            child: const Scaffold(
              body: SingleChildScrollView(
                child: Padding(
                  padding: EdgeInsets.all(44),
                  child: DailyEnergy(
                    diary: {
                      'spent_kcal': '2500',
                      'energy_delta': '-400',
                      'energy_delta_estimated': true,
                      'energy_delta_incomplete': true,
                    },
                  ),
                ),
              ),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('Разница: ≈ -400 ккал'), findsOneWidget);
      expect(
        find.text('По записанной еде: часть калорий ещё не рассчитана'),
        findsOneWidget,
      );
      expect(tester.takeException(), isNull);
    },
  );
}
