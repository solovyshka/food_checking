import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:food_checking_mobile/main.dart';

import 'fake_network.dart';

class PreviewApi extends Api {
  final requests = <Map<String, dynamic>>[];
  @override
  Future<Map<String, dynamic>> request(
    String method,
    String path, {
    Map<String, dynamic>? body,
    String? audio,
    String? image,
    bool public = false,
  }) async {
    requests.add({
      'method': method,
      'path': path,
      'body': body,
      'audio': audio,
      'image': image,
    });
    if (path == '/images') {
      return {'id': '12345678-1234-4234-9234-123456789012'};
    }
    if (path == '/queue') return {'id': 1, 'status': 'queued'};
    if (path == '/queue/parse' || path.startsWith('/queue/jobs/')) {
      return {
        'id': '12345678-1234-4234-9234-123456789013',
        'status': 'completed',
        'foods': <Map<String, dynamic>>[],
        'nutrition': <Map<String, dynamic>>[],
        'skipped': [],
      };
    }
    return {'items': <Map<String, dynamic>>[], 'skipped': []};
  }
}

class QueueApi extends Api {
  bool fail = true;
  final requests = <Map<String, dynamic>>[];
  @override
  Future<Map<String, dynamic>> request(
    String method,
    String path, {
    Map<String, dynamic>? body,
    String? audio,
    String? image,
    bool public = false,
  }) async {
    requests.add({'method': method, 'path': path, 'body': body});
    if (fail) throw const SocketException('offline');
    return {'id': 1, 'status': 'queued'};
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUp(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(device, (call) async => null);
  });
  testWidgets(
    'Queue survives a failed send and retries the identical saved text',
    (tester) async {
      final api = QueueApi();
      Map<String, dynamic>? stored;
      Future<void> open(Map<String, dynamic> initial) async {
        await tester.pumpWidget(
          MaterialApp(
            home: AddPage(
              key: UniqueKey(),
              api: api,
              date: '2026-10-03',
              initial: initial,
              onDraft: (data) async {
                stored = data;
                if (data != null) {
                  expect(api.requests.length, lessThanOrEqualTo(1));
                }
              },
            ),
          ),
        );
        await tester.pumpAndSettle();
      }

      await open({'text': 'Молоко 200 г', 'meal': 'breakfast'});
      await tester.scrollUntilVisible(
        find.text('В очередь на разбор'),
        200,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.tap(find.text('В очередь на разбор'));
      await tester.pumpAndSettle();
      expect(api.requests.single['path'], '/queue');
      final request = Map<String, dynamic>.from(stored!['pending_queue']);
      expect(request['text'], 'Молоко 200 г');
      expect(request.containsKey('items'), false);
      await open(Map<String, dynamic>.from(stored!));
      expect(tester.widget<TextField>(find.byType(TextField)).enabled, false);
      expect(find.text('Разобрать и рассчитать'), findsNothing);
      api.fail = false;
      await tester.scrollUntilVisible(
        find.text('Повторить отправку в очередь'),
        200,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.tap(find.text('Повторить отправку в очередь'));
      await tester.pumpAndSettle();
      expect(api.requests.length, 2);
      expect(api.requests.last['body'], request);
      expect(stored, isNull);
      expect(tester.takeException(), isNull);
    },
  );
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
      await tester.ensureVisible(
        find.byKey(const ValueKey('protein_per_100g')),
      );
      await tester.enterText(
        find.byKey(const ValueKey('protein_per_100g')),
        '3,25',
      );
      await tester.ensureVisible(find.byKey(const ValueKey('fat_per_100g')));
      await tester.enterText(find.byKey(const ValueKey('fat_per_100g')), '-1');
      await tester.tap(find.text('Готово'));
      await tester.pumpAndSettle();
      expect(result, isNull);
      await tester.ensureVisible(find.byKey(const ValueKey('fat_per_100g')));
      await tester.enterText(find.byKey(const ValueKey('fat_per_100g')), '0');
      await tester.tap(find.text('Готово'));
      await tester.pumpAndSettle();
      expect(result?['quantity'], '200.5');
      expect(result?['nutrition_source'], 'label');
      expect(result?['protein_per_100g'], '3.25');
      expect(result?['fat_per_100g'], '0');
      expect(result?['carbs_per_100g'], isNull);
      expect(result?['macros_source'], 'label');
    },
  );
  testWidgets('Manual macros reach the saved draft and entry request', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(360, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final api = PreviewApi();
    Map<String, dynamic>? stored;
    await tester.runAsync(() => FoodLibraryRepository(api).load());
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => Scaffold(
            body: TextButton(
              onPressed: () => Navigator.push<void>(
                context,
                MaterialPageRoute(
                  builder: (_) => AddPage(
                    api: api,
                    date: '2026-10-05',
                    onDraft: (draft) async {
                      stored = draft;
                    },
                  ),
                ),
              ),
              child: const Text('open'),
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.text('Добавить продукт'),
      200,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.text('Добавить продукт'));
    await tester.pump();
    // Assets and platform storage complete outside the test's fake clock.
    await tester.runAsync(() async {
      await Future<void>.delayed(const Duration(milliseconds: 100));
    });
    await tester.pumpAndSettle();
    await tester.tap(find.text('Вручную'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextFormField).at(0), 'Молоко 2.5%');
    await tester.enterText(find.byType(TextFormField).at(1), '200');
    await tester.ensureVisible(find.byType(TextFormField).at(2));
    await tester.enterText(find.byType(TextFormField).at(2), '52');
    for (final entry in {
      'protein_per_100g': '3,2',
      'fat_per_100g': '2,5',
      'carbs_per_100g': '4,7',
    }.entries) {
      final field = find.byKey(ValueKey(entry.key));
      await tester.ensureVisible(field);
      await tester.enterText(field, entry.value);
    }
    await tester.tap(find.text('Готово'));
    await tester.pumpAndSettle();
    final item = Map<String, dynamic>.from((stored!['items'] as List).single);
    expect(item['protein_per_100g'], '3.2');
    expect(item['fat_per_100g'], '2.5');
    expect(item['carbs_per_100g'], '4.7');
    expect(item['macros_source'], 'manual');
    await tester.scrollUntilVisible(
      find.text('Сохранить в дневник'),
      200,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.text('Сохранить в дневник'));
    await tester.pumpAndSettle();
    final request = api.requests.singleWhere((r) => r['path'] == '/entries');
    expect(request['path'], '/entries');
    expect((request['body']['items'] as List).single, item);
    expect(stored, isNull);
    expect(tester.takeException(), isNull);
  });

  testWidgets('Editing only a portion preserves estimated macros', (
    tester,
  ) async {
    Map<String, dynamic>? result;
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => Scaffold(
            body: TextButton(
              onPressed: () async {
                result = await itemDialog(context, {
                  'name': 'Котлета',
                  'quantity': '100',
                  'unit': 'г',
                  'kcal_per_100g': '280',
                  'nutrition_source': 'label',
                  'protein_per_100g': '15.00',
                  'fat_per_100g': '20.00',
                  'carbs_per_100g': '10.00',
                  'macros_source': 'estimate',
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
    await tester.enterText(find.byType(TextFormField).at(1), '200');
    await tester.tap(find.text('Готово'));
    await tester.pumpAndSettle();
    expect(result?['quantity'], '200');
    expect(result?['protein_per_100g'], '15.00');
    expect(result?['fat_per_100g'], '20.00');
    expect(result?['carbs_per_100g'], '10.00');
    expect(result?['macros_source'], 'estimate');
    expect(tester.takeException(), isNull);
  });

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
    expect(find.text('Тест Grok'), findsNothing);
    // A restored immutable request cannot accidentally be recalculated or edited.
    expect(jsonEncode(body), jsonEncode(draft['pending']));
    expect(tester.takeException(), isNull);
    // Ensure closure keeps the draft serializable.
    if (stored != null) expect(stored!['pending'], body);
  });

  testWidgets('Picture is selected, restored, replaced and removed locally', (
    tester,
  ) async {
    final directory = Directory.systemTemp.createTempSync('food-picture-test-');
    addTearDown(() => directory.deleteSync(recursive: true));
    final png = base64Decode(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=',
    );
    final first = File('${directory.path}/first.jpg')..writeAsBytesSync(png);
    final second = File('${directory.path}/second.jpg')..writeAsBytesSync(png);
    final deleted = <String>[];
    int selections = 0;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(device, (call) async {
          if (call.method == 'pickImage') {
            return selections++ == 0 ? first.path : second.path;
          }
          if (call.method == 'deleteImage') {
            deleted.add(call.arguments['path'] as String);
          }
          return null;
        });
    final api = PreviewApi();
    Map<String, dynamic>? stored;
    Future<void> open({Map<String, dynamic>? initial}) async {
      await tester.pumpWidget(
        MaterialApp(
          home: AddPage(
            key: UniqueKey(),
            api: api,
            date: '2026-10-03',
            initial: initial,
            onDraft: (data) async {
              stored = data;
            },
          ),
        ),
      );
      await tester.pumpAndSettle();
    }

    await open();
    await tester.tap(find.text('Добавить картинку'));
    await tester.pumpAndSettle();
    expect(stored?['image_path'], first.path);
    expect(api.requests, isEmpty);
    await tester.scrollUntilVisible(
      find.byKey(ValueKey(first.path)),
      200,
      scrollable: find
          .descendant(
            of: find.byType(ListView),
            matching: find.byType(Scrollable),
          )
          .first,
    );
    expect(find.byKey(ValueKey(first.path)), findsOneWidget);
    final restored = Map<String, dynamic>.from(stored!);
    await open(initial: restored);
    await tester.tap(find.text('Заменить картинку'));
    await tester.pumpAndSettle();
    expect(stored?['image_path'], second.path);
    expect(deleted, [first.path]);
    await tester.scrollUntilVisible(
      find.text('Убрать картинку'),
      200,
      scrollable: find
          .descendant(
            of: find.byType(ListView),
            matching: find.byType(Scrollable),
          )
          .first,
    );
    await tester.tap(find.text('Убрать картинку'));
    await tester.pumpAndSettle();
    expect(stored?['image_path'], isNull);
    expect(deleted, [first.path, second.path]);
    expect(api.requests, isEmpty);
    expect(tester.takeException(), isNull);
  });

  testWidgets('Cancelling picture selection leaves the draft unchanged', (
    tester,
  ) async {
    final api = PreviewApi();
    Map<String, dynamic>? stored;
    await tester.pumpWidget(
      MaterialApp(
        home: AddPage(
          api: api,
          date: '2026-10-03',
          initial: const {'text': 'Молоко 200 г'},
          onDraft: (data) async {
            stored = data;
          },
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('Добавить картинку'));
    await tester.pumpAndSettle();
    expect(
      tester.widget<TextField>(find.byType(TextField)).controller!.text,
      'Молоко 200 г',
    );
    expect(find.text('Добавить картинку'), findsOneWidget);
    expect(stored, isNull);
    expect(api.requests, isEmpty);
  });

  testWidgets(
    'Picture-only analysis uploads photo, queues and starts Grok without preview',
    (tester) async {
      final directory = Directory.systemTemp.createTempSync(
        'food-picture-test-',
      );
      addTearDown(() => directory.deleteSync(recursive: true));
      final image = File('${directory.path}/picture.jpg')
        ..writeAsBytesSync(
          base64Decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=',
          ),
        );
      final api = PreviewApi();
      Map<String, dynamic>? stored;
      await tester.pumpWidget(
        MaterialApp(
          home: AddPage(
            api: api,
            date: '2026-10-03',
            initial: {'image_path': image.path},
            onDraft: (data) async {
              stored = data;
            },
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
        find.text('Разобрать и рассчитать'),
        200,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.tap(find.text('Разобрать и рассчитать'));
      await tester.pumpAndSettle();
      expect(api.requests.map((r) => r['path']).take(3), [
        '/images',
        '/queue',
        '/queue/parse',
      ]);
      expect(api.requests[0]['image'], image.path);
      expect(
        api.requests[1]['body']['image_id'],
        '12345678-1234-4234-9234-123456789012',
      );
      expect(api.requests[1]['body']['text'], '');
      expect(api.requests[2]['body']['entry_ids'], [1]);
      expect(api.requests.any((r) => r['path'] == '/preview'), false);
      expect(stored, isNull);
      expect(find.text('Разбор сохранён в дневнике'), findsOneWidget);
      expect(find.text('Что съедено'), findsOneWidget);
      expect(find.text('Калорийность'), findsNothing);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('Food rows fit large text without horizontal scrolling', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(320, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    Map<String, dynamic>? edited;
    Map<String, dynamic>? deleted;
    await tester.pumpWidget(
      MaterialApp(
        home: MediaQuery(
          data: const MediaQueryData(textScaler: TextScaler.linear(2)),
          child: Scaffold(
            body: ListView(
              padding: const EdgeInsets.symmetric(horizontal: 20),
              children: [
                AnalysisTables(
                  onEdit: (item) => edited = item,
                  onDelete: (item) => deleted = item,
                  nutrition: const [
                    {
                      'food_id': 1,
                      'name': 'Котлета по-киевски с гарниром',
                      'quantity': '300',
                      'unit': 'г',
                      'kcal_per_100g': '280',
                      'kcal': '840',
                      'protein': '30',
                      'fat': '60',
                      'carbs': null,
                      'macros_source': 'estimate',
                      'portion_is_estimate': true,
                      'nutrition_source': 'estimate',
                      'note': 'Масса порции оценена приблизительно',
                    },
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Что съедено'), findsOneWidget);
    expect(find.text('Калорийность'), findsNothing);
    expect(find.byType(DataTable), findsNothing);
    expect(
      tester
          .widgetList<Scrollable>(find.byType(Scrollable))
          .every(
            (scrollable) => scrollable.axisDirection == AxisDirection.down,
          ),
      true,
    );
    expect(find.text('Котлета по-киевски с гарниром'), findsOneWidget);
    expect(find.text('Порция'), findsOneWidget);
    expect(find.text('≈ 300 г'), findsOneWidget);
    expect(find.text('Ккал / 100 г'), findsOneWidget);
    expect(find.text('≈ 280'), findsOneWidget);
    expect(find.text('Ккал итог'), findsOneWidget);
    expect(find.text('≈ 840'), findsOneWidget);
    expect(find.text('Б/Ж/У ≈ 30/60/— г'), findsOneWidget);
    expect(find.text('Расчёт'), findsNothing);
    expect(find.text('Дата · приём пищи'), findsNothing);
    expect(find.textContaining('Масса порции оценена'), findsNothing);
    final product = tester.getRect(find.text('Котлета по-киевски с гарниром'));
    final edit = tester.getRect(find.byTooltip('Изменить продукт'));
    final remove = tester.getRect(find.byTooltip('Удалить продукт'));
    expect(product.right, lessThanOrEqualTo(edit.left));
    expect(edit.right, lessThanOrEqualTo(remove.left));
    for (final text in [
      'Котлета по-киевски с гарниром',
      '≈ 300 г',
      '≈ 280',
      '≈ 840',
      'Б/Ж/У ≈ 30/60/— г',
    ]) {
      final bounds = tester.getRect(find.text(text));
      expect(bounds.left, greaterThanOrEqualTo(20));
      expect(bounds.right, lessThanOrEqualTo(300));
    }
    await tester.tap(find.byTooltip('Изменить продукт'));
    await tester.tap(find.byTooltip('Удалить продукт'));
    expect(edited?['food_id'], 1);
    expect(deleted?['food_id'], 1);
    expect(tester.takeException(), isNull);
  });

  testWidgets('Add page has queue and analysis buttons without Grok test', (
    tester,
  ) async {
    final api = PreviewApi();
    await tester.pumpWidget(
      MaterialApp(
        home: AddPage(
          api: api,
          date: '2026-10-03',
          initial: const {'text': 'Молоко 200 г'},
          onDraft: (_) async {},
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.text('Разобрать и рассчитать'),
      200,
      scrollable: find.byType(Scrollable).first,
    );
    expect(find.text('Тест Grok'), findsNothing);
    expect(find.text('В очередь на разбор'), findsOneWidget);
    expect(api.requests, isEmpty);
  });

  testWidgets(
    'Daily macros distinguish estimated, zero and unknown on a narrow screen',
    (tester) async {
      tester.view.physicalSize = const Size(360, 800);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      await tester.pumpWidget(
        MaterialApp(
          home: MediaQuery(
            data: const MediaQueryData(textScaler: TextScaler.linear(2)),
            child: Scaffold(
              body: Padding(
                padding: const EdgeInsets.all(44),
                child: DailyMacros(
                  diary: const {
                    'total_protein': '9.9',
                    'total_fat': '0.0',
                    'total_carbs': null,
                    'estimated_macros': {
                      'protein': true,
                      'fat': false,
                      'carbs': false,
                    },
                    'missing_macros': {'protein': 0, 'fat': 0, 'carbs': 1},
                  },
                ),
              ),
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('Белки: ≈ 9.9 г'), findsOneWidget);
      expect(find.text('Жиры: 0 г'), findsOneWidget);
      expect(find.text('Углеводы: —'), findsOneWidget);
      expect(find.text('БЖУ рассчитаны не для всех продуктов'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );
  testWidgets(
    'Server selection reuses connection and persists after reopening',
    (tester) async {
      final network = FakeNetwork();
      HttpOverrides.global = network;
      addTearDown(() => HttpOverrides.global = null);
      var stored = jsonEncode({
        'url': publicApiBase,
        'token': 'existing-test-token',
        'draft': {'text': 'Несохранённая еда'},
      });
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(device, (call) async {
            if (call.method == 'load') {
              return stored;
            }
            if (call.method == 'store') {
              stored = (call.arguments as Map)['value'] as String;
            }
            if (call.method == 'appInfo') {
              return {'versionCode': 9};
            }
            return null;
          });
      await tester.pumpWidget(const FoodApp());
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('Настройки'));
      await tester.pumpAndSettle();
      await tester.tap(find.byType(DropdownButtonFormField<ServerChoice>));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Русский сервер').last);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Сохранить'));
      await tester.pumpAndSettle();
      final state = jsonDecode(stored) as Map;
      expect(state['url'], publicApiFallback);
      expect(state['automatic_failover'], false);
      expect(state['token'], 'existing-test-token');
      expect(state['draft'], {'text': 'Несохранённая еда'});
      expect(
        network.requests.where((r) => r.uri.path.endsWith('/pair')),
        isEmpty,
      );
      expect(network.requests.last.uri.host, Uri.parse(publicApiFallback).host);
      await tester.pumpWidget(const SizedBox.shrink());
      await tester.pumpAndSettle();
      network.requests.clear();
      await tester.pumpWidget(const FoodApp());
      await tester.pumpAndSettle();
      expect(
        network.requests.every(
          (r) => r.uri.host == Uri.parse(publicApiFallback).host,
        ),
        true,
      );
      await tester.tap(find.byTooltip('Настройки'));
      await tester.pumpAndSettle();
      expect(
        tester
            .widget<DropdownButtonFormField<ServerChoice>>(
              find.byType(DropdownButtonFormField<ServerChoice>),
            )
            .initialValue,
        ServerChoice.russian,
      );
      expect(tester.takeException(), isNull);
    },
  );
  testWidgets(
    'Re-pairing cannot transfer an unsaved draft into another account',
    (tester) async {
      final network = FakeNetwork();
      HttpOverrides.global = network;
      addTearDown(() => HttpOverrides.global = null);
      var stored = jsonEncode({
        'url': publicApiBase,
        'token': 'first-user-token',
        'draft': {'text': 'Еда первого пользователя'},
      });
      network.responder = (request) => request.uri.path.endsWith('/pair')
          ? {'token': 'second-user-token'}
          : null;
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(device, (call) async {
            if (call.method == 'load') {
              return stored;
            }
            if (call.method == 'store') {
              stored = (call.arguments as Map)['value'] as String;
            }
            return null;
          });
      await tester.pumpWidget(const FoodApp());
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('Настройки'));
      await tester.pumpAndSettle();
      final input = find.byWidgetPredicate(
        (w) => w is TextField && w.decoration?.labelText == 'Код подключения',
      );
      await tester.ensureVisible(input);
      await tester.enterText(input, '87654321');
      await tester.tap(find.text('Сохранить'));
      await tester.pumpAndSettle();
      expect(
        find.text(
          'Сначала сохраните или очистите черновик перед сменой пользователя',
        ),
        findsOneWidget,
      );
      final state = jsonDecode(stored) as Map;
      expect(state['token'], 'first-user-token');
      expect(state['draft'], {'text': 'Еда первого пользователя'});
      expect(
        network.requests
            .where((r) => r.method == 'POST')
            .every((r) => r.uri.path.endsWith('/pair')),
        true,
      );
      await tester.tap(find.text('Закрыть'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    },
  );
  testWidgets(
    'Queue hides history while a current job can be checked and disappears after completion',
    (tester) async {
      final network = FakeNetwork();
      HttpOverrides.global = network;
      addTearDown(() => HttpOverrides.global = null);
      var finished = false;
      network.responder = (request) {
        if (request.uri.path.endsWith('/queue')) {
          return {
            'items': [
              {
                'id': 1,
                'entry_date': '2026-10-07',
                'meal': 'lunch',
                'text': 'Ждёт разбора',
                'status': 'queued',
              },
              if (!finished)
                {
                  'id': 2,
                  'entry_date': '2026-10-07',
                  'meal': 'dinner',
                  'text': 'Сейчас разбирается',
                  'status': 'processing',
                  'job_id': 'current-job',
                },
              {
                'id': 3,
                'entry_date': '2026-10-01',
                'meal': 'lunch',
                'text': 'Историческая запись',
                'status': 'parsed',
              },
            ],
            'jobs': [
              {'id': 'old-complete', 'status': 'completed'},
              {
                'id': 'old-failed',
                'status': 'failed',
                'error': 'Старая ошибка',
              },
            ],
          };
        }
        if (request.uri.path.endsWith('/queue/jobs/current-job')) {
          finished = true;
          return {
            'id': 'current-job',
            'status': 'completed',
            'nutrition': [],
            'skipped': [],
          };
        }
        return null;
      };
      await tester.pumpWidget(
        MaterialApp(home: QueuePage(api: Api()..token = 'test-token')),
      );
      await tester.pumpAndSettle();
      expect(find.text('Ждёт разбора'), findsOneWidget);
      expect(find.text('Историческая запись'), findsNothing);
      expect(find.text('Разбор сохранён в дневнике'), findsNothing);
      expect(find.text('Не удалось завершить разбор'), findsNothing);
      expect(find.text('Старая ошибка'), findsNothing);
      await tester.scrollUntilVisible(
        find.text('Проверить разбор'),
        160,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.tap(find.text('Проверить разбор'));
      await tester.pumpAndSettle();
      expect(find.byType(GrokJobPage), findsOneWidget);
      expect(find.text('Разбор сохранён в дневнике'), findsOneWidget);
      await tester.tap(find.text('Вернуться к дневнику'));
      await tester.pumpAndSettle();
      expect(find.text('Ждёт разбора'), findsOneWidget);
      expect(find.text('Сейчас разбирается'), findsNothing);
      expect(find.text('Проверить разбор'), findsNothing);
      expect(find.text('Разбор сохранён в дневнике'), findsNothing);
      expect(
        network.requests.where((request) => request.method != 'GET'),
        isEmpty,
      );
      expect(tester.takeException(), isNull);
    },
  );
}
