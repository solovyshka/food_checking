import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:food_checking_mobile/main.dart';

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
      expect(find.text('Калорийность'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('Both result tables fit a narrow phone at large text size', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(360, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      MaterialApp(
        home: MediaQuery(
          data: const MediaQueryData(textScaler: TextScaler.linear(2)),
          child: Scaffold(
            body: ListView(
              children: [
                AnalysisTables(
                  foods: const [
                    {
                      'id': 1,
                      'name': 'Котлета по-киевски с гарниром',
                      'amount': '2',
                      'unit': 'шт',
                      'entry_date': '2026-10-03',
                      'meal': 'обед',
                      'amount_is_estimate': false,
                    },
                  ],
                  nutrition: const [
                    {
                      'food_id': 1,
                      'name': 'Котлета по-киевски с гарниром',
                      'quantity': '300',
                      'unit': 'г',
                      'kcal_per_100g': '280',
                      'kcal': '840',
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
    expect(find.text('Калорийность'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('Grok test sends the draft text and does not calculate', (
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
      find.text('Тест Grok'),
      200,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.text('Тест Grok'));
    await tester.pumpAndSettle();
    expect(api.requests.single['path'], '/grok/test');
    expect(api.requests.single['body'], {
      'text': 'Молоко 200 г',
      'has_image': false,
    });
    expect(
      find.text('Grok принял тестовое сообщение. Ответ появится в чате бота.'),
      findsOneWidget,
    );
    expect(tester.takeException(), isNull);
  });
}
