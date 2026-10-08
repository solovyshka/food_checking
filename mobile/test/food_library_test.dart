import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:food_checking_mobile/main.dart';

class LibraryApi extends Api {
  final items = <String, Map<String, dynamic>>{};
  final requests = <Map<String, dynamic>>[];
  bool offline = false, loseSaveResponse = false;
  Completer<Map<String, dynamic>>? delayedList;
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
      'body': body == null ? null : jsonDecode(jsonEncode(body)),
    });
    if (offline) throw const SocketException('offline');
    if (method == 'GET') {
      return delayedList?.future ??
          {'items': items.values.toList(), 'catalog_version': 1};
    }
    final id = path.split('/').last;
    if (method == 'DELETE') {
      items.remove(id);
      return {'deleted': true};
    }
    final nutrients = body!['kind'] == 'recipe'
        ? recipeNutrition(
            (body['ingredients'] as List)
                .map((i) => Map<String, dynamic>.from(i))
                .toList(),
            foodValue(body['yield_g'])!,
          )
        : body['product'];
    final row = {
      'id': id,
      'kind': body['kind'],
      'revision': body['revision'] + 1,
      'data': {
        'name': body['name'],
        'unit': 'г',
        ...nutrients,
        if (body['kind'] == 'recipe') 'ingredients': body['ingredients'],
        if (body['kind'] == 'recipe') 'yield_g': body['yield_g'],
      },
    };
    items[id] = row;
    if (loseSaveResponse) {
      loseSaveResponse = false;
      throw const SocketException('response lost');
    }
    return row;
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  final storage = <String, String>{};
  setUp(() {
    storage.clear();
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(device, (call) async {
          if (call.method == 'loadLibrary') {
            return storage[call.arguments['owner']];
          }
          if (call.method == 'storeLibrary') {
            storage[call.arguments['owner']] = call.arguments['value'];
          }
          return null;
        });
  });
  test(
    'Short catalogue names, cooked grains, aliases, typos and recipe yield',
    () async {
      final raw = await rootBundle.loadString('assets/food_catalog.json');
      final catalog = decodeFoodCatalog(raw);
      final entries = (catalog['products'] as List)
          .map(
            (p) => FoodLibraryEntry.fromJson({
              'id': p['id'],
              'kind': 'catalog',
              'revision': 1,
              'data': p,
            }),
          )
          .toList();
      expect(entries.length, greaterThanOrEqualTo(150));
      for (final q in ['ГРЕЧКА', 'греч', 'гречко', 'гречневая']) {
        expect(findFoods(entries, q).first.name, 'Гречка');
      }
      expect(findFoods(entries, 'молоко 2.5%').first.name, 'Молоко 2,5%');
      expect(findFoods(entries, 'несуществующий продукт'), isEmpty);
      final grams = [
        {
          'quantity': '100',
          'kcal_per_100g': '340',
          'protein_per_100g': '12',
          'fat_per_100g': '3',
          'carbs_per_100g': '65',
        },
        {
          'quantity': '10',
          'kcal_per_100g': '900',
          'protein_per_100g': '0',
          'fat_per_100g': '100',
          'carbs_per_100g': '0',
        },
      ];
      expect(recipeNutrition(grams, 500), {
        'kcal_per_100g': '86.00',
        'protein_per_100g': '2.40',
        'fat_per_100g': '2.60',
        'carbs_per_100g': '13.00',
      });
      expect(recipeNutrition(grams, 1000)['kcal_per_100g'], '43.00');
    },
  );
  test('Recipe cache and draft survive restart offline and never cross users or custom servers', () async {
    final api = LibraryApi()..token = 'first';
    final repo = FoodLibraryRepository(api);
    await repo.load();
    final row = {
      'name': 'Мой продукт',
      'kcal_per_100g': '100',
      'protein_per_100g': '0',
      'fat_per_100g': '1',
      'carbs_per_100g': '24',
    };
    await repo.save('personal-id', {
      'request_id': 'request',
      'kind': 'product',
      'revision': 0,
      'name': 'Мой продукт',
      'product': row,
    });
    repo.recipeDraft = {'name': 'Черновик', 'ingredients': []};
    await repo.persist();
    api.offline = true;
    final restarted = FoodLibraryRepository(api);
    await restarted.load();
    expect(restarted.personal.single.name, 'Мой продукт');
    expect(restarted.recipeDraft!['name'], 'Черновик');
    await expectLater(restarted.sync(), throwsA(isA<SocketException>()));
    api.configure(publicApiFallback);
    final russian = FoodLibraryRepository(api);
    await russian.load();
    expect(russian.personal.single.name, 'Мой продукт');
    api.token = 'second';
    final other = FoodLibraryRepository(api);
    await other.load();
    expect(other.personal, isEmpty);
    expect(other.recipeDraft, isNull);
    api.token = 'first';
    api.configure('https://example.com');
    final custom = FoodLibraryRepository(api);
    await custom.load();
    expect(custom.personal, isEmpty);
  });
  test(
    'A delayed refresh cannot erase a product saved after the refresh began',
    () async {
      final api = LibraryApi()..token = 'owner';
      final repo = FoodLibraryRepository(api);
      await repo.load();
      api.delayedList = Completer();
      final refresh = repo.sync();
      await Future<void>.delayed(Duration.zero);
      await repo.save('id', {
        'request_id': 'request',
        'kind': 'product',
        'revision': 0,
        'name': 'Продукт',
        'product': {
          'name': 'Продукт',
          'kcal_per_100g': '10',
          'protein_per_100g': '0',
          'fat_per_100g': '0',
          'carbs_per_100g': '1',
        },
      });
      api.delayedList!.complete({'items': [], 'catalog_version': 1});
      await refresh;
      expect(repo.personal.single.name, 'Продукт');
    },
  );
  testWidgets(
    'Offline catalogue selection fits a narrow phone and returns an immutable portion',
    (tester) async {
      tester.view.physicalSize = const Size(360, 800);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final api = LibraryApi()..offline = true;
      final repo = FoodLibraryRepository(api);
      await tester.runAsync(repo.load);
      Map<String, dynamic>? result;
      await tester.pumpWidget(
        MaterialApp(
          home: Builder(
            builder: (context) => Scaffold(
              body: TextButton(
                onPressed: () async {
                  result = await Navigator.push<Map<String, dynamic>>(
                    context,
                    MaterialPageRoute(
                      builder: (_) =>
                          FoodPickerPage(api: api, repository: repo),
                    ),
                  );
                },
                child: const Text('open'),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField), 'гречко');
      await tester.pumpAndSettle();
      await tester.tap(find.text('Гречка'));
      await tester.pumpAndSettle();
      await tester.enterText(
        find.byKey(const ValueKey('food_portion')),
        '200,5',
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Добавить порцию'));
      await tester.pumpAndSettle();
      expect(result!['name'], 'Гречка');
      expect(result!['quantity'], '200.5');
      expect(result!['library_ref']['kind'], 'catalog');
      expect(result!['nutrition_source'], 'estimate');
      expect(result!['fat_per_100g'], isNotNull);
      expect(api.requests, isEmpty);
      expect(tester.takeException(), isNull);
    },
  );
  testWidgets(
    'A new recipe selects cooked ingredients and saves the finished yield',
    (tester) async {
      final api = LibraryApi()..token = 'owner';
      final repo = FoodLibraryRepository(api);
      await tester.runAsync(repo.load);
      await tester.pumpWidget(
        MaterialApp(
          home: Builder(
            builder: (context) => Scaffold(
              body: TextButton(
                onPressed: () => Navigator.push<void>(
                  context,
                  MaterialPageRoute(
                    builder: (_) => RecipeEditorPage(repository: repo),
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
      await tester.enterText(find.byType(TextFormField).first, 'Моя гречка');
      await tester.ensureVisible(find.text('Добавить ингредиент'));
      await tester.tap(find.text('Добавить ингредиент'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField), 'гречка');
      await tester.pumpAndSettle();
      await tester.tap(find.text('Гречка'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byKey(const ValueKey('food_portion')), '200');
      await tester.tap(find.text('Добавить порцию'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byKey(const ValueKey('recipe_yield')), '500');
      await tester.ensureVisible(find.text('Сохранить рецепт'));
      await tester.tap(find.text('Сохранить рецепт'));
      await tester.pumpAndSettle();
      expect(api.requests.single['body']['yield_g'], '500');
      expect(api.requests.single['body']['ingredients'][0]['name'], 'Гречка');
      expect(api.requests.single['body']['ingredients'][0]['quantity'], '200');
      expect(repo.personal.single.name, 'Моя гречка');
      expect(repo.recipeDraft, isNull);
      expect(tester.takeException(), isNull);
    },
  );
  testWidgets(
    'Recipe save lost response restores frozen draft and retries the exact request',
    (tester) async {
      final api = LibraryApi()
        ..token = 'owner'
        ..loseSaveResponse = true;
      final repo = FoodLibraryRepository(api);
      await tester.runAsync(repo.load);
      repo.recipeDraft = {
        'id': 'recipe-id',
        'revision': 0,
        'name': 'Каша',
        'yield_g': '500',
        'ingredients': [
          {
            'name': 'Гречка',
            'quantity': '200',
            'unit': 'г',
            'kcal_per_100g': '100',
            'protein_per_100g': '4',
            'fat_per_100g': '1',
            'carbs_per_100g': '20',
          },
        ],
      };
      Future<void> open() async {
        await tester.pumpWidget(
          MaterialApp(
            home: Builder(
              builder: (context) => Scaffold(
                body: TextButton(
                  onPressed: () => Navigator.push<void>(
                    context,
                    MaterialPageRoute(
                      builder: (_) => RecipeEditorPage(repository: repo),
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
      }

      await open();
      await tester.ensureVisible(find.text('Сохранить рецепт'));
      await tester.tap(find.text('Сохранить рецепт'));
      await tester.pumpAndSettle();
      expect(repo.recipeDraft!['pending'], isNotNull);
      final first = api.requests.single['body'];
      await tester.pumpWidget(const SizedBox());
      await tester.pumpAndSettle();
      await open();
      await tester.ensureVisible(find.text('Повторить сохранение'));
      await tester.tap(find.text('Повторить сохранение'));
      await tester.pumpAndSettle();
      expect(api.requests.last['body'], first);
      expect(repo.personal.single.name, 'Каша');
      expect(repo.personal.single.data['kcal_per_100g'], '40.00');
      expect(repo.recipeDraft, isNull);
      expect(find.text('open'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );
}
