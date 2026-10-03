import 'dart:async';
import 'dart:io';
import 'dart:math';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

class AppRelease {
  AppRelease.fromJson(Map<String, dynamic> json)
    : code = (json['versionCode'] as num).toInt(),
      name = json['versionName'] as String,
      size = (json['sizeBytes'] as num).toInt(),
      sha256 = json['sha256'] as String,
      path = json['apkPath'] as String {
    if (code <= 0 ||
        size <= 0 ||
        size > 200 * 1024 * 1024 ||
        !RegExp(r'^[a-f0-9]{64}$').hasMatch(sha256) ||
        path != '/app/releases/$code-${sha256.substring(0, 16)}/update.bin') {
      throw const FormatException('Некорректная информация об обновлении');
    }
  }
  final int code;
  final String name;
  final int size;
  final String sha256;
  final String path;
}

/// Range requests resume a pinned, immutable APK; bytes from releases cannot mix.
Future<File> downloadUpdate({
  required AppRelease release,
  required List<String> doors,
  required Directory directory,
  void Function(int, int)? onProgress,
  int chunkSize = 1024 * 1024,
}) async {
  await directory.create(recursive: true);
  final destination = File(
    '${directory.path}/food-${release.code}-${release.sha256.substring(0, 16)}.apk',
  );
  if (await destination.exists() &&
      await destination.length() == release.size) {
    onProgress?.call(release.size, release.size);
    return destination;
  }
  final partial = File('${destination.path}.part');
  int received = await partial.exists() ? await partial.length() : 0;
  if (received > release.size) {
    await partial.delete();
    received = 0;
  }
  onProgress?.call(received, release.size);
  final file = await partial.open(mode: FileMode.writeOnlyAppend);
  final client = HttpClient()..connectionTimeout = const Duration(seconds: 10);
  try {
    while (received < release.size) {
      final end = min(received + chunkSize, release.size) - 1;
      Uint8List? piece;
      Object? lastError;
      for (int attempt = 0; attempt < doors.length * 2; attempt++) {
        final door = doors[attempt % doors.length];
        try {
          final request = await client.getUrl(
            Uri.parse('$door${release.path}'),
          );
          request.followRedirects = false;
          request.headers.set('Range', 'bytes=$received-$end');
          request.headers.set('Cache-Control', 'no-cache');
          request.headers.set('Accept-Encoding', 'identity');
          final response = await request.close().timeout(
            const Duration(seconds: 30),
          );
          final expected = 'bytes $received-$end/${release.size}';
          if (response.statusCode != 206 ||
              response.headers.value('content-range') != expected) {
            await response.drain<void>().timeout(const Duration(seconds: 10));
            throw Exception('Сервер не подтвердил диапазон загрузки');
          }
          final bytes = BytesBuilder(copy: false);
          await for (final part in response.timeout(
            const Duration(seconds: 25),
          )) {
            bytes.add(part);
            if (bytes.length > end - received + 1) {
              throw Exception('Неверный размер части обновления');
            }
          }
          if (bytes.length != end - received + 1) {
            throw Exception('Сеть оборвала загрузку');
          }
          piece = bytes.takeBytes();
          break;
        } catch (e) {
          lastError = e;
        }
      }
      if (piece == null) {
        throw Exception(
          'Не удалось загрузить обновление. Нажмите повторно — продолжим с места остановки. ${lastError ?? ''}',
        );
      }
      await file.writeFrom(piece);
      await file.flush();
      received += piece.length;
      onProgress?.call(received, release.size);
    }
  } finally {
    await file.close();
    client.close(force: true);
  }
  if (await destination.exists()) await destination.delete();
  return partial.rename(destination.path);
}

class UpdateBanner extends StatefulWidget {
  const UpdateBanner({
    super.key,
    required this.release,
    required this.doors,
    this.channel = const MethodChannel('ru.solovyshka.food_checking/device'),
  });
  final AppRelease release;
  final List<String> doors;
  final MethodChannel channel;
  @override
  State<UpdateBanner> createState() => _UpdateBannerState();
}

class _UpdateBannerState extends State<UpdateBanner> {
  bool busy = false;
  int received = 0;
  String? error;
  File? downloaded;
  bool permissionNeeded = false;
  Future<void> run(String preferred) async {
    setState(() {
      busy = true;
      error = null;
    });
    try {
      if (downloaded == null) {
        final path = await widget.channel.invokeMethod<String>(
          'updatesDirectory',
        );
        if (path == null) throw Exception('Не удалось подготовить загрузку');
        downloaded = await downloadUpdate(
          release: widget.release,
          doors: <String>{preferred, ...widget.doors}.toList(),
          directory: Directory(path),
          onProgress: (value, total) {
            if (mounted) setState(() => received = value);
          },
        );
        try {
          await widget.channel.invokeMethod('verifyUpdate', {
            'path': downloaded!.path,
            'sha256': widget.release.sha256,
            'versionCode': widget.release.code,
          });
        } catch (_) {
          await downloaded!.delete();
          downloaded = null;
          rethrow;
        }
      }
      if (!mounted) return;
      final result = await widget.channel.invokeMethod<String>(
        'installUpdate',
        {'path': downloaded!.path},
      );
      if (mounted) {
        setState(() => permissionNeeded = result == 'permission_required');
      }
    } catch (e) {
      if (mounted) {
        setState(
          () => error = e is PlatformException
              ? e.message
              : e.toString().replaceFirst('Exception: ', ''),
        );
      }
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Card(
    color: Colors.white,
    child: Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Доступна версия ${widget.release.name}',
            style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 17),
          ),
          const SizedBox(height: 8),
          if (downloaded == null)
            Wrap(
              spacing: 8,
              children: widget.doors
                  .map(
                    (door) => FilledButton.tonalIcon(
                      onPressed: busy ? null : () => run(door),
                      icon: const Icon(Icons.download_rounded),
                      label: Text(
                        door.contains('vladislavsolovei.ru')
                            ? 'Скачать · RU'
                            : 'Скачать · OVH',
                      ),
                    ),
                  )
                  .toList(),
            )
          else
            FilledButton.icon(
              onPressed: busy ? null : () => run(widget.doors.first),
              icon: const Icon(Icons.system_update_rounded),
              label: const Text('Установить обновление'),
            ),
          if (busy) ...[
            const SizedBox(height: 12),
            LinearProgressIndicator(value: received / widget.release.size),
            const SizedBox(height: 6),
            Text(
              '${(received / 1048576).toStringAsFixed(1)} из ${(widget.release.size / 1048576).toStringAsFixed(1)} МБ',
            ),
          ],
          if (permissionNeeded)
            const Padding(
              padding: EdgeInsets.only(top: 10),
              child: Text(
                'Разрешите установку обновлений для «Еда» в настройках Android. Затем нажмите «Установить обновление».',
              ),
            ),
          if (error != null)
            Padding(
              padding: const EdgeInsets.only(top: 10),
              child: Text(error!, style: const TextStyle(color: Colors.red)),
            ),
        ],
      ),
    ),
  );
}
