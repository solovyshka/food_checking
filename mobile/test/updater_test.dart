import 'dart:async';

import 'package:flutter/services.dart';

import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:food_checking_mobile/updater.dart';

Map<String, dynamic> releaseJson(int size) => {
  'versionCode': 4,
  'versionName': '1.1.0',
  'sizeBytes': size,
  'sha256': 'a' * 64,
  'apkPath': '/app/releases/4-${'a' * 16}/update.bin',
};
void main() {
  setUp(() {
    HttpOverrides.global = null;
  });
  test('Release pins the file checksum, code and immutable path', () {
    expect(AppRelease.fromJson(releaseJson(100)).code, 4);
    expect(
      () => AppRelease.fromJson({
        ...releaseJson(100),
        'apkPath': '/app/update.bin',
      }),
      throwsFormatException,
    );
    expect(
      () => AppRelease.fromJson({...releaseJson(100), 'sizeBytes': 0}),
      throwsFormatException,
    );
  });
  test(
    'Range downloader resumes a complete chunk after interruption',
    () async {
      final directory = await Directory.systemTemp.createTemp(
        'food-update-test-',
      );
      final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      final bytes = List.generate(100, (i) => i);
      bool interrupted = true;
      final starts = <int>[];
      final subscription = server.listen((request) async {
        final range = RegExp(r'bytes=(\d+)-(\d+)')
            .firstMatch(request.headers.value('range')!)!;
        final start = int.parse(range[1]!);
        final end = int.parse(range[2]!);
        starts.add(start);
        if (interrupted && start >= 20) {
          request.response.statusCode = 503;
          await request.response.close();
          return;
        }
        request.response.statusCode = 206;
        request.response.headers.set('Content-Range', 'bytes $start-$end/100');
        request.response.add(bytes.sublist(start, end + 1));
        await request.response.close();
      });
      try {
        final release = AppRelease.fromJson(releaseJson(100));
        final doors = ['http://127.0.0.1:${server.port}'];
        await expectLater(
          downloadUpdate(
            release: release,
            doors: doors,
            directory: directory,
            chunkSize: 20,
          ),
          throwsException,
        );
        final part = directory.listSync().whereType<File>().single;
        expect(await part.length(), 20);
        interrupted = false;
        starts.clear();
        final file = await downloadUpdate(
          release: release,
          doors: doors,
          directory: directory,
          chunkSize: 20,
        );
        expect(starts.first, 20);
        expect(await file.readAsBytes(), bytes);
        expect(directory.listSync().whereType<File>().length, 1);
      } finally {
        await subscription.cancel();
        await server.close(force: true);
        await directory.delete(recursive: true);
      }
    },
  );
  test(
    'Downloader falls back and rejects a server that ignores ranges',
    () async {
      final directory = await Directory.systemTemp.createTemp(
        'food-update-test-',
      );
      final broken = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      final good = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      final bytes = List.generate(12, (i) => i);
      final a = broken.listen((request) async {
        request.response.add(bytes);
        await request.response.close();
      });
      final b = good.listen((request) async {
        request.response.statusCode = 206;
        request.response.headers.set('Content-Range', 'bytes 0-11/12');
        request.response.add(bytes);
        await request.response.close();
      });
      try {
        final file = await downloadUpdate(
          release: AppRelease.fromJson(releaseJson(12)),
          doors: [
            'http://127.0.0.1:${broken.port}',
            'http://127.0.0.1:${good.port}',
          ],
          directory: directory,
        );
        expect(await file.readAsBytes(), bytes);
      } finally {
        await a.cancel();
        await b.cancel();
        await broken.close(force: true);
        await good.close(force: true);
        await directory.delete(recursive: true);
      }
    },
  );
  testWidgets(
    'Downloaded update offers installer and explains Android permission',
    (tester) async {
      late Directory directory;
      late HttpServer server;
      late StreamSubscription<HttpRequest> subscription;
      await tester.runAsync(() async {
        directory = await Directory.systemTemp.createTemp(
          'food-update-widget-',
        );
        server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
        subscription = server.listen((request) async {
          request.response.statusCode = 206;
          request.response.headers.set('Content-Range', 'bytes 0-11/12');
          request.response.add(List.generate(12, (i) => i));
          await request.response.close();
        });
      });
      const channel = MethodChannel('food.update.test');
      final installed = Completer<void>();
      bool verified = false;
      int installerCalls = 0;
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(channel, (call) async {
            if (call.method == 'updatesDirectory') return directory.path;
            if (call.method == 'verifyUpdate') {
              expect(call.arguments['versionCode'], 4);
              expect(call.arguments['sha256'], 'a' * 64);
              verified = true;
              return null;
            }
            if (call.method == 'installUpdate') {
              expect(verified, true);
              installerCalls++;
              if (!installed.isCompleted) installed.complete();
              return installerCalls == 1
                  ? 'permission_required'
                  : 'installer_opened';
            }
            return null;
          });
      try {
        await tester.pumpWidget(
          MaterialApp(
            home: Scaffold(
              body: UpdateBanner(
                channel: channel,
                release: AppRelease.fromJson(releaseJson(12)),
                doors: ['http://127.0.0.1:${server.port}'],
              ),
            ),
          ),
        );
        await tester.runAsync(() async {
          HttpOverrides.global = null;
          await (tester.state(find.byType(UpdateBanner)) as dynamic).run(
            'http://127.0.0.1:${server.port}',
          );
          expect(
            (tester.state(find.byType(UpdateBanner)) as dynamic).error,
            isNull,
          );
          expect(installed.isCompleted, true);
        });
        await tester.pumpAndSettle();
        expect(find.text('Установить обновление'), findsOneWidget);
        expect(
          find.textContaining('Разрешите установку обновлений'),
          findsOneWidget,
        );
        await tester.tap(find.text('Установить обновление'));
        await tester.pumpAndSettle();
        expect(installerCalls, 2);
        expect(
          find.textContaining('Разрешите установку обновлений'),
          findsNothing,
        );
      } finally {
        TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
            .setMockMethodCallHandler(channel, null);
        await tester.pumpWidget(const SizedBox());
        await tester.runAsync(() async {
          await subscription.cancel();
          await server.close(force: true);
          await directory.delete(recursive: true);
        });
      }
    },
  );
  testWidgets('Update banner has OVH and RU download controls', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: UpdateBanner(
            release: AppRelease.fromJson(releaseJson(100)),
            doors: const [
              'https://vladislavsolovei.ru/food-consumption',
              'https://food-consumption.solovyshka.com',
            ],
          ),
        ),
      ),
    );
    expect(find.text('Доступна версия 1.1.0'), findsOneWidget);
    expect(find.text('Скачать · RU'), findsOneWidget);
    expect(find.text('Скачать · OVH'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
