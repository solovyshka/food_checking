import 'dart:io';
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:food_checking_mobile/main.dart';

class BarcodeApi extends Api {
  final calls = <Map<String, dynamic>>[];
  bool failLabel = false;
  Completer<Map<String, dynamic>>? deferredLabel;
  final product = <String, dynamic>{
    'barcode': '03017620422003',
    'name': 'Фабричный продукт',
    'unit': 'г',
    'kcal_per_100g': '100',
    'protein_per_100g': '4',
    'fat_per_100g': '0',
    'carbs_per_100g': null,
    'verified': false,
  };
  @override
  Future<Map<String, dynamic>> request(
    String method,
    String path, {
    Map<String, dynamic>? body,
    String? audio,
    String? image,
    bool public = false,
  }) async {
    calls.add({'method': method, 'path': path, 'body': body});
    if (path == '/images') return {'id': 'image-id'};
    if (path == '/barcodes/labels') {
      final deferred = deferredLabel;
      if (deferred != null) {
        deferredLabel = null;
        return deferred.future;
      }
      if (failLabel) throw const SocketException('lost response');
      return {
        'id': body!['request_id'],
        'status': 'completed',
        'product': product,
      };
    }
    if (path.startsWith('/barcodes/products/')) {
      return {'found': true, 'product': product};
    }
    return {
      'ids': [1],
    };
  }
}

Finder field(String label) => find.widgetWithText(TextFormField, label);

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUp(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
          device,
          (call) async => call.method == 'scanBarcode' ? '3017620422003' : null,
        );
  });

  testWidgets('An old screen response cannot overwrite a resumed label task', (
    tester,
  ) async {
    final deferred = Completer<Map<String, dynamic>>();
    final api = BarcodeApi()..deferredLabel = deferred;
    Map<String, dynamic>? stored;
    Future<void> open(Map<String, dynamic> value) async {
      await tester.pumpWidget(
        MaterialApp(
          home: BarcodePage(
            key: UniqueKey(),
            api: api,
            initial: value,
            onDraft: (value) async => stored = value,
          ),
        ),
      );
      await tester.pumpAndSettle();
    }

    await open({
      'code': '3017620422003',
      'canonical': '03017620422003',
      'image_id': 'image-id',
      'image_path': '/saved-label.jpg',
    });
    await tester.ensureVisible(find.text('Продолжить разбор выбранного фото'));
    await tester.tap(find.text('Продолжить разбор выбранного фото'));
    await tester.pump();
    final pending = Map<String, dynamic>.from(stored!['pending_label']);
    // Dispose the sending screen while its response is still in flight.
    await tester.pumpWidget(const MaterialApp(home: SizedBox()));
    await open(Map<String, dynamic>.from(stored!));
    await tester.ensureVisible(find.text('Продолжить отправку этикетки'));
    await tester.tap(find.text('Продолжить отправку этикетки'));
    await tester.pumpAndSettle();
    expect(stored!['job']['status'], 'completed');
    deferred.complete({'id': pending['request_id'], 'status': 'running'});
    await tester.pumpAndSettle();
    expect(stored!['job']['status'], 'completed');
    expect(stored!['pending_label'], isNull);
    final sends = api.calls
        .where((call) => call['path'] == '/barcodes/labels')
        .toList();
    expect(sends.length, 2);
    expect(sends[0]['body'], pending);
    expect(sends[1]['body'], pending);
    expect(tester.takeException(), isNull);
  });

  testWidgets('Scanned nutrition becomes a draft; only Save logs a portion', (
    tester,
  ) async {
    final api = BarcodeApi();
    Map<String, dynamic>? draft;
    await tester.pumpWidget(
      MaterialApp(
        home: AddPage(
          api: api,
          date: '2026-10-05',
          onDraft: (value) async => draft = value,
        ),
      ),
    );
    await tester.scrollUntilVisible(
      find.text('Добавить по штрихкоду'),
      200,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.text('Добавить по штрихкоду'));
    await tester.pumpAndSettle();
    expect(find.text('Фабричный продукт'), findsOneWidget);
    await tester.ensureVisible(find.text('Проверить и указать порцию'));
    await tester.tap(find.text('Проверить и указать порцию'));
    await tester.pumpAndSettle();
    await tester.enterText(field('Количество'), '250');
    await tester.ensureVisible(find.text('Значения сверены с упаковкой'));
    await tester.tap(find.text('Значения сверены с упаковкой'));
    await tester.tap(find.text('Готово'));
    await tester.pumpAndSettle();
    final put = api.calls.singleWhere((call) => call['method'] == 'PUT');
    expect(put['body']['verified'], true);
    expect(put['body']['fat_per_100g'], '0');
    expect(put['body']['carbs_per_100g'], isNull);
    expect(api.calls.any((call) => call['path'] == '/entries'), false);
    final item = (draft!['items'] as List).single;
    expect(item['barcode'], '03017620422003');
    expect(item['quantity'], '250');
    expect(item['macros_source'], 'label');
    expect(draft!['barcode_draft'], isNull);
    await tester.scrollUntilVisible(
      find.text('Сохранить в дневник'),
      200,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.text('Сохранить в дневник'));
    await tester.pumpAndSettle();
    final sent = api.calls.singleWhere((call) => call['path'] == '/entries');
    expect(sent['body']['items'][0]['barcode'], '03017620422003');
    expect(sent['body']['items'][0]['fat_per_100g'], '0');
    expect(tester.takeException(), isNull);
  });

  testWidgets(
    'A lost label response resumes the same immutable request without uploading again',
    (tester) async {
      final api = BarcodeApi()..failLabel = true;
      Map<String, dynamic>? stored;
      final initial = {
        'code': '3017620422003',
        'canonical': '03017620422003',
        'image_id': 'image-id',
        'image_path': '/saved-label.jpg',
      };
      Future<void> open(Map<String, dynamic> value) async {
        await tester.pumpWidget(
          MaterialApp(
            home: BarcodePage(
              key: UniqueKey(),
              api: api,
              initial: value,
              onDraft: (value) async => stored = value,
            ),
          ),
        );
        await tester.pumpAndSettle();
      }

      await open(initial);
      await tester.ensureVisible(
        find.text('Продолжить разбор выбранного фото'),
      );
      await tester.tap(find.text('Продолжить разбор выбранного фото'));
      await tester.pumpAndSettle();
      expect(stored!['pending_label'], isNotNull);
      final request = Map<String, dynamic>.from(stored!['pending_label']);
      api.failLabel = false;
      await open(Map<String, dynamic>.from(stored!));
      await tester.ensureVisible(find.text('Продолжить отправку этикетки'));
      await tester.tap(find.text('Продолжить отправку этикетки'));
      await tester.pumpAndSettle();
      final sends = api.calls
          .where((call) => call['path'] == '/barcodes/labels')
          .toList();
      expect(sends.length, 2);
      expect(sends[0]['body'], request);
      expect(sends[1]['body'], request);
      expect(
        api.calls.any(
          (call) => call['path'] == '/images' || call['path'] == '/entries',
        ),
        false,
      );
      expect(stored!['job']['status'], 'completed');
      expect(stored!['product']['fat_per_100g'], '0');
      expect(stored!['pending_label'], isNull);
      expect(tester.takeException(), isNull);
    },
  );
}
