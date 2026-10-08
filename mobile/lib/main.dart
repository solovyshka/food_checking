import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart' show compute;
import 'package:flutter/services.dart';
import 'package:flutter_localizations/flutter_localizations.dart';

import 'updater.dart';

part 'barcodes.dart';
part 'daily_energy.dart';
part 'food_library.dart';

const device = MethodChannel('ru.solovyshka.food_checking/device');
const green = Color(0xFF234F42);
const paper = Color(0xFFF7F5EE);
const meals = {
  'breakfast': 'Завтрак',
  'lunch': 'Обед',
  'dinner': 'Ужин',
  'snack': 'Перекус',
};
String dayKey(DateTime d) =>
    '${d.year}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';
String number(dynamic v) {
  final n = double.tryParse('$v');
  if (n == null) return '—';
  return n == n.roundToDouble() ? n.toStringAsFixed(0) : n.toStringAsFixed(1);
}

String uuid() {
  final bytes = List.generate(16, (_) => Random.secure().nextInt(256));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  final s = bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  return '${s.substring(0, 8)}-${s.substring(8, 12)}-${s.substring(12, 16)}-${s.substring(16, 20)}-${s.substring(20)}';
}

void main() => runApp(const FoodApp());

class FoodApp extends StatelessWidget {
  const FoodApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
    debugShowCheckedModeBanner: false,
    title: 'Еда',
    locale: const Locale('ru'),
    supportedLocales: const [Locale('ru')],
    localizationsDelegates: GlobalMaterialLocalizations.delegates,
    theme: ThemeData(
      useMaterial3: true,
      scaffoldBackgroundColor: paper,
      colorScheme: ColorScheme.fromSeed(
        seedColor: green,
        primary: green,
        surface: paper,
      ),
      appBarTheme: const AppBarTheme(backgroundColor: paper, elevation: 0),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: Colors.white,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: BorderSide.none,
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 16),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(16),
          ),
        ),
      ),
    ),
    home: const DiaryPage(),
  );
}

const publicApiBase = 'https://food-consumption.solovyshka.com';
const publicApiFallback = 'https://vladislavsolovei.ru/food-consumption';

bool isPublicDoor(String address) =>
    address == publicApiBase || address == publicApiFallback;

enum ServerChoice { automatic, russian, french, custom }

String serverLabel(ServerChoice choice) => switch (choice) {
  ServerChoice.automatic => 'Автоматически',
  ServerChoice.russian => 'Русский сервер',
  ServerChoice.french => 'Французский сервер (OVH)',
  ServerChoice.custom => 'Другой адрес',
};

class _DoorUnavailable implements Exception {
  const _DoorUnavailable(this.message);
  final String message;
  @override
  String toString() => message;
}

class Api {
  String url = publicApiBase;
  String token = '';
  bool automaticFailover = true;
  String? _activeDoor;
  bool get connected => token.isNotEmpty;
  String get activeUrl => _activeDoor ?? url;
  ServerChoice get serverChoice => !isPublicDoor(url)
      ? ServerChoice.custom
      : automaticFailover
      ? ServerChoice.automatic
      : url == publicApiFallback
      ? ServerChoice.russian
      : ServerChoice.french;
  List<String> get requestDoors => automaticFailover && isPublicDoor(url)
      ? <String>{
          if (_activeDoor != null) _activeDoor!,
          url,
          publicApiBase,
          publicApiFallback,
        }.toList()
      : [url];
  void configure(
    String address, {
    String? activeDoor,
    bool automaticFailover = true,
  }) {
    url = address;
    this.automaticFailover = automaticFailover;
    _activeDoor =
        activeDoor == address ||
            (automaticFailover &&
                isPublicDoor(address) &&
                activeDoor != null &&
                isPublicDoor(activeDoor))
        ? activeDoor
        : null;
  }

  Future<Map<String, dynamic>> request(
    String method,
    String path, {
    Map<String, dynamic>? body,
    String? audio,
    String? image,
    bool public = false,
  }) async {
    // Explicit server choices never silently switch to another door.
    final doors = requestDoors;
    Object? last;
    for (final door in doors) {
      try {
        final data = await _requestDoor(
          door,
          method,
          path,
          body: body,
          audio: audio,
          image: image,
          public: public,
        );
        _activeDoor = door;
        return data;
      } on _DoorUnavailable catch (e) {
        last = e;
      }
    }
    throw Exception(errorText(last ?? 'Сервер недоступен'));
  }

  Future<Map<String, dynamic>> _requestDoor(
    String door,
    String method,
    String path, {
    Map<String, dynamic>? body,
    String? audio,
    String? image,
    required bool public,
  }) async {
    final client = HttpClient()..connectionTimeout = const Duration(seconds: 8);
    try {
      final req = await client.openUrl(
        method,
        Uri.parse('$door${public ? '' : '/api/mobile'}$path'),
      );
      req.followRedirects = false;
      if (!public && token.isNotEmpty) {
        req.headers.set('Authorization', 'Bearer $token');
      }
      if (audio != null || image != null) {
        final boundary = 'food-${uuid()}';
        req.headers.contentType = ContentType(
          'multipart',
          'form-data',
          parameters: {'boundary': boundary},
        );
        req.add(
          utf8.encode(
            '--$boundary\r\nContent-Disposition: form-data; name="file"; filename="${image != null ? 'photo.jpg' : 'voice.m4a'}"\r\nContent-Type: ${image != null ? 'image/jpeg' : 'audio/mp4'}\r\n\r\n',
          ),
        );
        req.add(await File(image ?? audio!).readAsBytes());
        req.add(utf8.encode('\r\n--$boundary--\r\n'));
      } else if (body != null) {
        req.headers.contentType = ContentType.json;
        req.add(utf8.encode(jsonEncode(body)));
      }
      final response = await req.close().timeout(
        Duration(seconds: audio != null ? 660 : 180),
      );
      if ([301, 302, 307, 308, 502, 503, 504].contains(response.statusCode)) {
        throw const _DoorUnavailable(
          'Сервер сейчас недоступен. Попробуйте ещё раз',
        );
      }
      final raw = await response
          .transform(utf8.decoder)
          .join()
          .timeout(const Duration(seconds: 30));
      final data = jsonDecode(raw) as Map<String, dynamic>;
      if (response.statusCode >= 400) {
        throw Exception(
          data['detail'] is String
              ? data['detail']
              : 'Проверьте введённые значения',
        );
      }
      return data;
    } on SocketException {
      throw const _DoorUnavailable(
        'Не удалось подключиться. Проверьте интернет и повторите попытку',
      );
    } on HandshakeException {
      throw const _DoorUnavailable(
        'Не удалось установить защищённое соединение',
      );
    } on TimeoutException {
      throw const _DoorUnavailable(
        'Ответ не пришёл вовремя. Попробуйте ещё раз',
      );
    } on FormatException {
      throw const _DoorUnavailable(
        'Сервер вернул неожиданный ответ. Проверьте адрес',
      );
    } on HttpException {
      throw const _DoorUnavailable(
        'Соединение с сервером прервалось. Попробуйте ещё раз',
      );
    } finally {
      client.close(force: true);
    }
  }
}

String errorText(Object e) => e.toString().replaceFirst('Exception: ', '');

class DiaryPage extends StatefulWidget {
  const DiaryPage({super.key});
  @override
  State<DiaryPage> createState() => _DiaryPageState();
}

class _DiaryPageState extends State<DiaryPage> {
  final api = Api();
  DateTime day = DateTime.now();
  Map<String, dynamic>? diary;
  AppRelease? update;
  Map<String, dynamic>? draft;
  bool loading = true;
  String? error;
  int revision = 0;
  @override
  void initState() {
    super.initState();
    restore();
  }

  Future<void> restore() async {
    try {
      final raw = await device.invokeMethod<String>('load', {'key': 'state'});
      if (raw != null) {
        final data = jsonDecode(raw) as Map<String, dynamic>;
        final address = data['url'] ?? api.url;
        api.configure(
          address == 'http://192.168.100.41:8092' ? publicApiBase : address,
          automaticFailover: data['automatic_failover'] as bool? ?? true,
        );
        api.token = data['token'] ?? '';
        draft = data['draft'] as Map<String, dynamic>?;
      }
    } catch (_) {
      /* First launch / device storage unavailable. */
    }
    await refresh();
    await checkUpdates(silent: true);
  }

  Future<void> persist() async {
    await device.invokeMethod('store', {
      'key': 'state',
      'value': jsonEncode({
        'url': api.url,
        'token': api.token,
        'draft': draft,
        'automatic_failover': api.automaticFailover,
      }),
    });
  }

  Future<void> updateDraft(Map<String, dynamic>? value) async {
    draft = value;
    await persist();
    if (mounted) setState(() {});
  }

  Future<void> refresh() async {
    final current = ++revision;
    final key = dayKey(day);
    if (!api.connected) {
      if (mounted) {
        setState(() {
          loading = false;
          diary = null;
          error = null;
        });
      }
      return;
    }
    if (mounted) {
      setState(() {
        loading = true;
        error = null;
        diary = null;
      });
    }
    try {
      final value = await api.request('GET', '/diary?entry_date=$key');
      if (mounted && current == revision) {
        setState(() {
          diary = value;
          loading = false;
        });
      }
    } catch (e) {
      if (mounted && current == revision) {
        setState(() {
          error = errorText(e);
          loading = false;
        });
      }
    }
  }

  Future<void> settings() async {
    final url = TextEditingController(text: api.url);
    final code = TextEditingController();
    var choice = api.serverChoice;
    bool canReuseToken(String address) =>
        api.connected &&
        (address == api.url ||
            (isPublicDoor(address) && isPublicDoor(api.url)));
    bool busy = false;
    String? problem;
    await showDialog<void>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, change) => AlertDialog(
          title: const Text('Настройки'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (api.connected) ...[
                  OutlinedButton.icon(
                    onPressed: busy
                        ? null
                        : () async {
                            final saved = await energyProfileDialog(
                              context,
                              api,
                            );
                            if (saved == true && mounted) await refresh();
                          },
                    icon: const Icon(Icons.monitor_weight_outlined),
                    label: const Text('Вес и расход в покое'),
                  ),
                  const SizedBox(height: 18),
                ],
                Text(
                  api.connected
                      ? 'Выберите, через какой сервер подключаться к коробке.'
                      : 'Выберите сервер и введите свой код доступа.',
                ),
                const SizedBox(height: 18),
                DropdownButtonFormField<ServerChoice>(
                  initialValue: choice,
                  isExpanded: true,
                  decoration: const InputDecoration(labelText: 'Сервер'),
                  items: ServerChoice.values
                      .map(
                        (value) => DropdownMenuItem(
                          value: value,
                          child: Text(
                            serverLabel(value),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(fontSize: 14),
                          ),
                        ),
                      )
                      .toList(),
                  onChanged: busy
                      ? null
                      : (value) {
                          if (value == null) return;
                          change(() {
                            choice = value;
                            problem = null;
                            if (value != ServerChoice.custom) {
                              url.text = value == ServerChoice.russian
                                  ? publicApiFallback
                                  : publicApiBase;
                            }
                          });
                        },
                ),
                const SizedBox(height: 8),
                Text(
                  choice == ServerChoice.automatic
                      ? 'Приложение выберет доступный сервер.'
                      : choice == ServerChoice.custom
                      ? 'Укажите адрес своего сервера.'
                      : 'Данные будут загружаться только через выбранный сервер.',
                  style: const TextStyle(color: Colors.black54, fontSize: 12),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: url,
                  readOnly: choice != ServerChoice.custom,
                  onChanged: (_) => change(() => problem = null),
                  enabled: !busy,
                  keyboardType: TextInputType.url,
                  decoration: const InputDecoration(labelText: 'Адрес сервера'),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: code,
                  enabled: !busy,
                  keyboardType: TextInputType.number,
                  obscureText: true,
                  decoration: InputDecoration(
                    labelText: 'Код подключения',
                    helperText: canReuseToken(url.text)
                        ? 'Можно не вводить повторно'
                        : null,
                  ),
                ),
                if (problem != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 12),
                    child: Text(
                      problem!,
                      style: const TextStyle(color: Colors.red),
                    ),
                  ),
                const SizedBox(height: 12),
                const Text(
                  'Записи хранятся на вашей коробке.',
                  style: TextStyle(fontSize: 12, color: Colors.black54),
                ),
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: busy ? null : () => Navigator.pop(ctx),
              child: const Text('Закрыть'),
            ),
            FilledButton(
              onPressed: busy
                  ? null
                  : () async {
                      final address = url.text.trim().replaceAll(
                        RegExp(r'/+$'),
                        '',
                      );
                      final uri = Uri.tryParse(address);
                      if (uri == null ||
                          uri.host.isEmpty ||
                          uri.userInfo.isNotEmpty ||
                          uri.query.isNotEmpty ||
                          uri.fragment.isNotEmpty ||
                          !RegExp(r'^(/[a-zA-Z0-9_-]+)*$').hasMatch(uri.path) ||
                          !(uri.scheme == 'https' ||
                              (uri.scheme == 'http' &&
                                  uri.host == '192.168.100.41'))) {
                        change(
                          () => problem =
                              'Укажите HTTPS-адрес или домашний адрес коробки',
                        );
                        return;
                      }
                      change(() {
                        busy = true;
                        problem = null;
                      });
                      final candidate = Api()
                        ..configure(
                          address,
                          automaticFailover: choice == ServerChoice.automatic,
                        );
                      final oldUrl = api.url;
                      final oldToken = api.token;
                      final oldDoor = api.activeUrl;
                      final oldFailover = api.automaticFailover;
                      final oldDraft = draft;
                      try {
                        if (code.text.trim().isEmpty &&
                            canReuseToken(address)) {
                          candidate.token = api.token;
                        } else {
                          final value = await candidate.request(
                            'POST',
                            '/pair',
                            body: {'code': code.text.trim()},
                          );
                          candidate.token = value['token'];
                        }
                        api.configure(
                          address,
                          activeDoor: candidate.activeUrl,
                          automaticFailover: candidate.automaticFailover,
                        );
                        if (oldToken.isNotEmpty &&
                            oldToken != candidate.token &&
                            draft != null) {
                          throw Exception(
                            'Сначала сохраните или очистите черновик перед сменой пользователя',
                          );
                        }
                        api.token = candidate.token;
                        await persist();
                        if (ctx.mounted) Navigator.pop(ctx);
                        await refresh();
                      } catch (e) {
                        api.configure(
                          oldUrl,
                          activeDoor: oldDoor,
                          automaticFailover: oldFailover,
                        );
                        api.token = oldToken;
                        draft = oldDraft;
                        if (ctx.mounted) {
                          change(() {
                            busy = false;
                            problem = errorText(e);
                          });
                        }
                      }
                    },
              child: Text(
                busy
                    ? 'Сохраняем…'
                    : api.connected
                    ? 'Сохранить'
                    : 'Подключить',
              ),
            ),
          ],
        ),
      ),
    );
    // Controllers may remain attached during the closing animation.
  }

  Future<void> checkUpdates({bool silent = false}) async {
    try {
      final info = await device.invokeMapMethod<String, dynamic>('appInfo');
      final localCode = info?['versionCode'] as int? ?? 18;
      final remote = await api.request(
        'GET',
        '/app/version.json',
        public: true,
      );
      if (!mounted) return;
      final release = AppRelease.fromJson(remote);
      if (release.code > localCode) {
        setState(() => update = release);
      } else if (!silent) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Установлена последняя версия')),
        );
      }
    } catch (e) {
      if (!silent && mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(errorText(e))));
      }
    }
  }

  Future<void> add() async {
    if (!api.connected) {
      await settings();
      if (!api.connected) return;
    }
    if (!mounted) return;
    final message = await Navigator.push<String>(
      context,
      MaterialPageRoute<String>(
        builder: (_) => AddPage(
          api: api,
          date: dayKey(day),
          initial: draft,
          onDraft: updateDraft,
        ),
      ),
    );
    await refresh();
    if (message != null && mounted) {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(message)));
    }
  }

  Future<void> edit(Map<String, dynamic> item) async {
    final updated = await itemDialog(context, item);
    if (updated == null) return;
    try {
      await api.request(
        'PATCH',
        '${item['grok'] == true ? '/grok/foods' : '/entries'}/${item['id']}',
        body: {...updated, if (item['grok'] != true) 'meal': item['meal']},
      );
      await refresh();
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(errorText(e))));
      }
    }
  }

  Future<void> remove(Map<String, dynamic> item) async {
    final yes = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Удалить запись?'),
        content: Text(item['name']),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Оставить'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Удалить'),
          ),
        ],
      ),
    );
    if (yes != true) return;
    try {
      await api.request(
        'DELETE',
        '${item['grok'] == true ? '/grok/foods' : '/entries'}/${item['id']}',
      );
      await refresh();
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(errorText(e))));
      }
    }
  }

  Future<void> editEnergy() async {
    final date = dayKey(day);
    final saved = await dailyEnergyDialog(
      context,
      api,
      date,
      diary?['training_kcal'],
    );
    if (saved == true && mounted) await refresh();
  }

  void moveDay(int offset) {
    setState(() => day = day.add(Duration(days: offset)));
    refresh();
  }

  @override
  Widget build(BuildContext context) {
    final items = (diary?['items'] as List? ?? []).cast<Map<String, dynamic>>();
    final foods = (diary?['foods'] as List? ?? []).cast<Map<String, dynamic>>();
    final nutrition = (diary?['nutrition'] as List? ?? [])
        .cast<Map<String, dynamic>>();
    final today = dayKey(day) == dayKey(DateTime.now());
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'еда.',
          style: TextStyle(
            fontWeight: FontWeight.w800,
            fontSize: 30,
            letterSpacing: -1.5,
          ),
        ),
        actions: [
          IconButton(
            tooltip: 'Обновить',
            onPressed: refresh,
            icon: const Icon(Icons.refresh_rounded),
          ),
          IconButton(
            tooltip: 'Проверить обновления',
            onPressed: () => checkUpdates(),
            icon: const Icon(Icons.system_update_rounded),
          ),
          IconButton(
            tooltip: 'Настройки',
            onPressed: settings,
            icon: const Icon(Icons.tune_rounded),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: refresh,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 8, 20, 120),
          children: [
            if (diary?['user']?['name'] != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: Text(
                  'Дневник: ${diary!['user']['name']}',
                  style: const TextStyle(color: Colors.black54),
                ),
              ),
            if (update != null)
              UpdateBanner(
                key: ValueKey(update!.sha256),
                release: update!,
                doors: api.requestDoors,
              ),
            Row(
              children: [
                IconButton(
                  onPressed: () => moveDay(-1),
                  icon: const Icon(Icons.chevron_left),
                ),
                Expanded(
                  child: TextButton(
                    onPressed: () async {
                      final selected = await showDatePicker(
                        context: context,
                        initialDate: day,
                        firstDate: DateTime(2000),
                        lastDate: DateTime(2100),
                      );
                      if (selected != null) {
                        setState(() => day = selected);
                        await refresh();
                      }
                    },
                    child: Text(
                      today
                          ? 'Сегодня, ${day.day}.${day.month.toString().padLeft(2, '0')}'
                          : '${day.day}.${day.month.toString().padLeft(2, '0')}.${day.year}',
                      style: const TextStyle(fontSize: 17),
                    ),
                  ),
                ),
                IconButton(
                  onPressed: () => moveDay(1),
                  icon: const Icon(Icons.chevron_right),
                ),
              ],
            ),
            const SizedBox(height: 16),
            Container(
              padding: const EdgeInsets.all(24),
              decoration: BoxDecoration(
                color: green,
                borderRadius: BorderRadius.circular(28),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'СЪЕДЕНО ЗА ДЕНЬ',
                    style: TextStyle(
                      color: Color(0xFFCEE0D6),
                      fontSize: 11,
                      letterSpacing: 2,
                    ),
                  ),
                  const SizedBox(height: 12),
                  Text(
                    '${diary == null ? '—' : number(diary!['total_kcal'])} ккал',
                    style: const TextStyle(
                      color: Colors.white,
                      fontSize: 40,
                      fontWeight: FontWeight.w600,
                      letterSpacing: -1,
                    ),
                  ),
                  const SizedBox(height: 12),
                  DailyMacros(diary: diary),
                  if (api.connected) ...[
                    const SizedBox(height: 16),
                    DailyEnergy(diary: diary),
                  ],
                  const SizedBox(height: 10),
                  Text(
                    diary == null
                        ? 'Ваш дневник питания'
                        : '${items.length + foods.length} записей',
                    style: const TextStyle(color: Color(0xFFCEE0D6)),
                  ),
                  if ((diary?['missing_kcal'] ?? 0) > 0)
                    Text(
                      '${diary!['missing_kcal']} без калорийности — не входят в сумму',
                      style: const TextStyle(color: Colors.white),
                    ),
                ],
              ),
            ),
            if (api.connected) ...[
              const SizedBox(height: 12),
              OutlinedButton.icon(
                onPressed: loading || error != null ? null : editEnergy,
                icon: const Icon(Icons.local_fire_department_outlined),
                label: Text(
                  diary?['training_kcal'] == null
                      ? 'Ввести калории от тренировок'
                      : 'Изменить калории от тренировок',
                ),
              ),
            ],
            const SizedBox(height: 26),
            if (api.connected)
              Padding(
                padding: const EdgeInsets.only(bottom: 16),
                child: OutlinedButton.icon(
                  onPressed: () => Navigator.push(
                    context,
                    MaterialPageRoute<void>(
                      builder: (_) => QueuePage(api: api),
                    ),
                  ).then((_) => refresh()),
                  icon: const Icon(Icons.pending_actions),
                  label: Text(
                    'Очередь на разбор${(diary?['queued_count'] ?? 0) > 0 ? ' · ${diary!['queued_count']} за этот день' : ''}',
                  ),
                ),
              ),
            if (loading)
              const Padding(
                padding: EdgeInsets.all(30),
                child: Center(child: CircularProgressIndicator()),
              )
            else if (!api.connected)
              emptyPanel(
                Icons.link_rounded,
                'Подключим дневник',
                'Записи и расчёты будут храниться на вашей коробке.',
                settings,
                'Подключиться',
              )
            else if (error != null)
              emptyPanel(
                Icons.wifi_off_rounded,
                'Не удалось загрузить день',
                error!,
                refresh,
                'Повторить',
              )
            else if (items.isEmpty && foods.isEmpty)
              emptyPanel(
                Icons.restaurant_rounded,
                'Что было вкусного?',
                'Скажите или напишите, что съели. Проверим продукты и запишем в дневник.',
                add,
                'Добавить еду',
              ),
            if (!loading &&
                error == null &&
                (items.isNotEmpty || nutrition.isNotEmpty))
              AnalysisTables(
                nutrition: [
                  ...nutrition.map(
                    (row) => {...row, 'id': row['food_id'], 'grok': true},
                  ),
                  ...items,
                ],
                onEdit: edit,
                onDelete: remove,
              ),
            if (draft != null)
              TextButton.icon(
                onPressed: add,
                icon: const Icon(Icons.edit_note),
                label: const Text('Продолжить несохранённую запись'),
              ),
            const Padding(
              padding: EdgeInsets.only(top: 10),
              child: Text(
                '≈ — оценка блюда. Значения с этикетки можно указать при проверке продуктов.',
                style: TextStyle(fontSize: 12, color: Colors.black54),
              ),
            ),
          ],
        ),
      ),
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(20, 8, 20, 12),
          child: FilledButton.icon(
            onPressed: add,
            icon: const Icon(Icons.add),
            label: Text(draft != null ? 'Продолжить запись' : 'Записать еду'),
          ),
        ),
      ),
    );
  }

  Widget emptyPanel(
    IconData icon,
    String title,
    String subtitle,
    VoidCallback action,
    String label,
  ) => Container(
    padding: const EdgeInsets.all(24),
    decoration: BoxDecoration(
      color: Colors.white,
      borderRadius: BorderRadius.circular(24),
    ),
    child: Column(
      children: [
        Icon(icon, color: green, size: 42),
        const SizedBox(height: 16),
        Text(
          title,
          textAlign: TextAlign.center,
          style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w600),
        ),
        const SizedBox(height: 10),
        Text(
          subtitle,
          textAlign: TextAlign.center,
          style: const TextStyle(color: Colors.black54),
        ),
        const SizedBox(height: 14),
        TextButton(onPressed: action, child: Text(label)),
      ],
    ),
  );
}

String sourceLabel(dynamic source) => switch (source) {
  'label' => 'по этикетке',
  'manual' => 'вручную',
  'estimate' => 'оценка',
  _ => 'источник не указан',
};

class AddPage extends StatefulWidget {
  const AddPage({
    super.key,
    required this.api,
    required this.date,
    required this.onDraft,
    this.initial,
  });
  final Api api;
  final String date;
  final Map<String, dynamic>? initial;
  final Future<void> Function(Map<String, dynamic>?) onDraft;
  @override
  State<AddPage> createState() => _AddPageState();
}

class _AddPageState extends State<AddPage> with WidgetsBindingObserver {
  late final TextEditingController text;
  late String date;
  String meal = 'snack';
  List<Map<String, dynamic>> items = [];
  Map<String, dynamic>? pending;
  Map<String, dynamic>? pendingQueue;
  Map<String, dynamic>? barcodeDraft;
  bool get locked => pending != null || pendingQueue != null;
  bool busy = false;
  bool recording = false;
  bool saved = false;
  String? error;
  String? notice;
  String? audioPath;
  String? imagePath;
  String? imageId;
  Map<String, dynamic>? pendingParse;
  bool parseNow = false;
  Timer? timer;
  int seconds = 0;
  Timer? saveTimer;
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    final draft = widget.initial;
    date = draft?['entry_date'] ?? widget.date;
    meal =
        draft?['meal'] ??
        (DateTime.now().hour < 11
            ? 'breakfast'
            : DateTime.now().hour < 16
            ? 'lunch'
            : 'dinner');
    text = TextEditingController(text: draft?['text'] ?? '');
    items = (draft?['items'] as List? ?? [])
        .map((i) => Map<String, dynamic>.from(i))
        .toList();
    pending = draft?['pending'] as Map<String, dynamic>?;
    pendingQueue = draft?['pending_queue'] as Map<String, dynamic>?;
    barcodeDraft = draft?['barcode_draft'] as Map<String, dynamic>?;
    pendingParse = draft?['pending_parse'] as Map<String, dynamic>?;
    parseNow = draft?['parse_now'] == true;
    imageId = draft?['image_id'] as String?;
    final storedImage = draft?['image_path'] as String?;
    if (storedImage != null && File(storedImage).existsSync()) {
      imagePath = storedImage;
    } else if (storedImage != null) {
      error = 'Картинка из черновика недоступна. Выберите её заново';
    }
    text.addListener(() {
      if (mounted) setState(() => items = []);
      saveTimer?.cancel();
      saveTimer = Timer(const Duration(milliseconds: 400), keepDraft);
    });
  }

  Map<String, dynamic> draftData() => {
    'entry_date': date,
    'meal': meal,
    'text': text.text,
    'items': items,
    'pending': pending,
    'pending_queue': pendingQueue,
    'image_path': imagePath,
    'image_id': imageId,
    'pending_parse': pendingParse,
    'parse_now': parseNow,
    'barcode_draft': barcodeDraft,
  };

  Future<bool> keepDraft() async {
    if (saved) return true;
    try {
      await widget.onDraft(draftData());
      return true;
    } catch (e) {
      if (mounted) {
        setState(() => error = 'Не удалось сохранить черновик на телефоне');
      }
      return false;
    }
  }

  Future<void> deleteImage(String? path) async {
    if (path == null) return;
    try {
      await device.invokeMethod('deleteImage', {'path': path});
    } catch (_) {}
  }

  Future<void> pickImage() async {
    final previous = imagePath;
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final selected = await device.invokeMethod<String>('pickImage');
      if (selected == null) return;
      if (!mounted) {
        await deleteImage(selected);
        return;
      }
      setState(() {
        imagePath = selected;
        imageId = null;
      });
      if (await keepDraft()) {
        await deleteImage(previous);
      }
    } catch (e) {
      if (mounted) {
        setState(
          () => error = e is PlatformException ? e.message : errorText(e),
        );
      }
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> removeImage() async {
    final previous = imagePath;
    setState(() {
      busy = true;
      imagePath = null;
      imageId = null;
    });
    if (await keepDraft()) {
      await deleteImage(previous);
    } else if (mounted) {
      setState(() => imagePath = previous);
      await keepDraft();
    }
    if (mounted) setState(() => busy = false);
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.inactive ||
        state == AppLifecycleState.paused) {
      keepDraft();
      if (recording) cancelRecording();
    }
  }

  Future<void> cancelRecording() async {
    timer?.cancel();
    try {
      await device.invokeMethod('cancelRecording');
    } catch (_) {}
    if (mounted) {
      setState(() {
        recording = false;
        error = 'Запись остановлена. Повторите её, когда приложение открыто';
      });
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    timer?.cancel();
    saveTimer?.cancel();
    if (recording) device.invokeMethod('cancelRecording');
    text.dispose();
    super.dispose();
  }

  Future<void> voice() async {
    if (!recording) {
      try {
        await device.invokeMethod('startRecording');
        if (!mounted) return;
        setState(() {
          recording = true;
          seconds = 0;
          error = null;
        });
        timer = Timer.periodic(const Duration(seconds: 1), (_) {
          if (mounted) setState(() => seconds++);
          if (seconds >= 120) voice();
        });
      } catch (e) {
        if (mounted) {
          setState(
            () => error = e is PlatformException ? e.message : errorText(e),
          );
        }
      }
      return;
    }
    timer?.cancel();
    setState(() {
      recording = false;
      busy = true;
    });
    try {
      audioPath = await device.invokeMethod<String>('stopRecording');
      await transcribe();
    } catch (e) {
      if (mounted) {
        setState(
          () => error = e is PlatformException ? e.message : errorText(e),
        );
      }
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> transcribe() async {
    final result = await widget.api.request(
      'POST',
      '/transcribe',
      audio: audioPath!,
    );
    text.text = [
      text.text.trim(),
      result['text'],
    ].where((s) => s.isNotEmpty).join(' ');
    items = [];
    error = null;
    try {
      await File(audioPath!).delete();
    } catch (_) {}
    audioPath = null;
    await keepDraft();
  }

  Future<void> calculate() => enqueue(analyze: true);

  Future<void> changeItem(int? index) async {
    final value = index == null
        ? await Navigator.of(context).push<Map<String, dynamic>>(
            MaterialPageRoute(builder: (_) => FoodPickerPage(api: widget.api)),
          )
        : await itemDialog(context, items[index]);
    if (value != null && mounted) {
      setState(() {
        if (index == null) {
          items.add(value);
        } else {
          items[index] = value;
        }
      });
      await keepDraft();
    }
  }

  Future<void> addBarcode() async {
    final value = await Navigator.of(context).push<Map<String, dynamic>>(
      MaterialPageRoute(
        builder: (_) => BarcodePage(
          api: widget.api,
          initial: barcodeDraft,
          onDraft: (data) async {
            barcodeDraft = data;
            if (!await keepDraft()) {
              throw Exception('Не удалось сохранить черновик');
            }
            if (mounted) setState(() {});
          },
        ),
      ),
    );
    if (value != null && mounted) {
      final image = value.remove('label_image_path') as String?;
      setState(() {
        items.add(value);
        barcodeDraft = null;
      });
      if (await keepDraft()) await deleteImage(image);
    }
  }

  Future<void> save() async {
    if (barcodeDraft != null) {
      setState(() => error = 'Сначала завершите добавление по штрихкоду');
      return;
    }
    if (pendingQueue != null) return;
    if (items.isEmpty && !locked) return;
    for (final i in items) {
      final qty = double.tryParse('${i['quantity']}');
      if (qty == null || !qty.isFinite || qty <= 0 || qty > 100000) {
        setState(() => error = 'Уточните вес каждого продукта');
        return;
      }
    }
    saveTimer?.cancel();
    setState(() {
      busy = true;
      error = null;
    });
    pending ??= {
      'request_id': uuid(),
      'entry_date': date,
      'meal': meal,
      'text': text.text,
      'items': items
          .map(
            (i) => {
              ...i,
              'kcal_per_100g': '${i['kcal_per_100g'] ?? ''}'.isEmpty
                  ? null
                  : i['kcal_per_100g'],
            },
          )
          .toList(),
    };
    try {
      // Persist the same request before sending: retry after timeout cannot duplicate food.
      await widget.onDraft(draftData());
      await widget.api.request('POST', '/entries', body: pending);
      saved = true;
      await widget.onDraft(null);
      await deleteImage(imagePath);
      if (mounted) Navigator.pop(context);
    } catch (e) {
      if (mounted) {
        setState(
          () => error =
              '${errorText(e)}. Повторное сохранение не создаст дубликат',
        );
      }
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> enqueue({bool analyze = false}) async {
    if (barcodeDraft != null) {
      setState(() => error = 'Сначала завершите добавление по штрихкоду');
      return;
    }
    if (pending != null) return;
    if (pendingQueue == null &&
        text.text.trim().isEmpty &&
        imagePath == null &&
        imageId == null) {
      setState(() => error = 'Напишите, что съели, или добавьте картинку');
      return;
    }
    saveTimer?.cancel();
    setState(() {
      busy = true;
      error = null;
    });
    var draftStored = false;
    try {
      if (pendingQueue == null) {
        parseNow = analyze;
        if (imagePath != null && imageId == null) {
          if (!await keepDraft()) {
            throw Exception('Не удалось сохранить черновик');
          }
          final uploaded = await widget.api.request(
            'POST',
            '/images',
            image: imagePath,
          );
          imageId = uploaded['id'] as String;
        }
        pendingQueue = {
          'request_id': uuid(),
          'entry_date': date,
          'meal': meal,
          'text': text.text.trim(),
          if (imageId != null) 'image_id': imageId,
        };
      }
      await widget.onDraft(draftData());
      draftStored = true;
      final entry = await widget.api.request(
        'POST',
        '/queue',
        body: pendingQueue,
      );
      Map<String, dynamic>? job;
      if (parseNow) {
        pendingParse ??= {
          'request_id': uuid(),
          'entry_ids': [entry['id']],
        };
        await widget.onDraft(draftData());
        job = await widget.api.request(
          'POST',
          '/queue/parse',
          body: pendingParse,
        );
      }
      await widget.onDraft(null);
      saved = true;
      await deleteImage(imagePath);
      if (!mounted) return;
      if (job != null) {
        Navigator.pushReplacement<String, String>(
          context,
          MaterialPageRoute<String>(
            builder: (_) => GrokJobPage(
              api: widget.api,
              jobId: job!['id'] as String,
              initial: job,
            ),
          ),
        );
      } else {
        Navigator.pop(context, 'Запись сохранена в очереди на разбор');
      }
    } catch (e) {
      if (mounted) {
        setState(
          () => error = draftStored
              ? '${errorText(e)}. Запись сохранена в черновике; повторная отправка не создаст дубликат'
              : 'Не удалось отправить запись. Черновик остался на телефоне: ${errorText(e)}',
        );
      }
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => PopScope(
    canPop: !busy && !recording,
    onPopInvokedWithResult: (didPop, _) {
      if (didPop) {
        keepDraft();
      }
    },
    child: Scaffold(
      appBar: AppBar(title: const Text('Записать еду')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(20, 12, 20, 30),
        children: [
          Text(date, style: const TextStyle(color: Colors.black54)),
          const SizedBox(height: 12),
          DropdownButtonFormField<String>(
            initialValue: meal,
            decoration: const InputDecoration(labelText: 'Приём пищи'),
            items: meals.entries
                .map(
                  (m) => DropdownMenuItem(value: m.key, child: Text(m.value)),
                )
                .toList(),
            onChanged: busy || locked || recording
                ? null
                : (v) {
                    setState(() => meal = v!);
                    keepDraft();
                  },
          ),
          const SizedBox(height: 22),
          const Text(
            'Что вы съели?',
            style: TextStyle(fontSize: 26, fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 14),
          TextField(
            controller: text,
            enabled: !busy && !locked && !recording,
            minLines: 3,
            maxLines: 7,
            maxLength: 8000,
            decoration: const InputDecoration(
              hintText: 'Например: молоко 2,5% — 200 г и котлета по-киевски',
            ),
          ),
          if (!locked) ...[
            const SizedBox(height: 8),
            OutlinedButton.icon(
              onPressed: busy ? null : voice,
              style: OutlinedButton.styleFrom(
                padding: const EdgeInsets.all(16),
                foregroundColor: recording ? Colors.red : green,
              ),
              icon: Icon(
                recording ? Icons.stop_circle : Icons.mic_none_rounded,
              ),
              label: Text(
                recording ? 'Остановить · $seconds с' : 'Сказать голосом',
              ),
            ),
            const SizedBox(height: 8),
            OutlinedButton.icon(
              onPressed: busy || recording ? null : pickImage,
              icon: const Icon(Icons.add_photo_alternate_outlined),
              label: Text(
                imagePath == null ? 'Добавить картинку' : 'Заменить картинку',
              ),
            ),
            const SizedBox(height: 8),
            OutlinedButton.icon(
              onPressed: busy || recording ? null : addBarcode,
              icon: const Icon(Icons.qr_code_scanner),
              label: Text(
                barcodeDraft == null
                    ? 'Добавить по штрихкоду'
                    : 'Продолжить добавление по штрихкоду',
              ),
            ),
            if (audioPath != null && !busy)
              TextButton(
                onPressed: () async {
                  setState(() => busy = true);
                  try {
                    await transcribe();
                  } catch (e) {
                    if (mounted) setState(() => error = errorText(e));
                  } finally {
                    if (mounted) setState(() => busy = false);
                  }
                },
                child: const Text('Повторить распознавание записи'),
              ),
            const SizedBox(height: 12),
            FilledButton.icon(
              onPressed: busy || recording ? null : () => enqueue(),
              icon: const Icon(Icons.playlist_add),
              label: const Text('В очередь на разбор'),
            ),
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 8),
              child: Text(
                'Сохранить текст и фото. Накопленные записи можно разобрать вместе.',
                style: TextStyle(color: Colors.black54, fontSize: 12),
              ),
            ),
            FilledButton(
              onPressed: busy || recording ? null : calculate,
              child: Text('Разобрать и рассчитать'),
            ),
          ],
          if (imagePath != null) ...[
            const SizedBox(height: 16),
            ClipRRect(
              borderRadius: BorderRadius.circular(16),
              child: Image.file(
                File(imagePath!),
                key: ValueKey(imagePath!),
                height: 200,
                fit: BoxFit.contain,
                semanticLabel: 'Картинка еды в черновике',
                errorBuilder: (_, _, _) => const SizedBox(
                  height: 100,
                  child: Center(
                    child: Text('Картинка недоступна. Выберите её заново'),
                  ),
                ),
              ),
            ),
            const SizedBox(height: 8),
            const Text(
              'Картинка сохранится вместе с записью. Калории по фото — приблизительная оценка.',
              style: TextStyle(color: Colors.black54, fontSize: 12),
            ),
            if (!locked)
              TextButton.icon(
                onPressed: busy || recording ? null : removeImage,
                icon: const Icon(Icons.close),
                label: const Text('Убрать картинку'),
              ),
          ],
          if (busy)
            const Padding(
              padding: EdgeInsets.all(22),
              child: Column(
                children: [
                  CircularProgressIndicator(),
                  SizedBox(height: 12),
                  Text('Обрабатываем… Это может занять минуту'),
                ],
              ),
            ),
          if (error != null)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 16),
              child: Text(
                error!,
                style: const TextStyle(color: Color(0xFFB53C2D)),
              ),
            ),
          if (notice != null)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 16),
              child: Text(notice!, style: const TextStyle(color: green)),
            ),
          if (pending != null)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 12),
              child: Text(
                'Запрос подготовлен к сохранению. Повторите отправку, чтобы подтвердить результат.',
              ),
            ),
          if (items.isNotEmpty) ...[
            const SizedBox(height: 24),
            const Text(
              'Проверьте продукты',
              style: TextStyle(fontSize: 22, fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 8),
            const Text(
              'Нажмите на продукт, чтобы уточнить порцию и калории на 100 г / мл.',
              style: TextStyle(color: Colors.black54),
            ),
            const SizedBox(height: 14),
            ...List.generate(items.length, (index) {
              final i = items[index];
              final qty = double.tryParse('${i['quantity']}');
              final kcal = double.tryParse('${i['kcal_per_100g']}');
              return Card(
                color: Colors.white,
                elevation: 0,
                margin: const EdgeInsets.only(bottom: 10),
                child: ListTile(
                  onTap: busy || locked ? null : () => changeItem(index),
                  title: Text(i['name']),
                  subtitle: Text(
                    '${number(i['quantity'])} ${i['unit']} · ${sourceLabel(i['nutrition_source'])}\n${number(i['kcal_per_100g'])} ккал / 100 ${i['unit']}',
                  ),
                  isThreeLine: true,
                  trailing: !locked
                      ? IconButton(
                          tooltip: 'Убрать продукт',
                          onPressed: busy
                              ? null
                              : () {
                                  setState(() => items.removeAt(index));
                                  keepDraft();
                                },
                          icon: const Icon(Icons.close),
                        )
                      : null,
                  leading: Text(
                    qty != null && kcal != null
                        ? '${number(qty * kcal / 100)}\nккал'
                        : '—',
                    textAlign: TextAlign.center,
                  ),
                ),
              );
            }),
          ],
          if (!locked)
            TextButton.icon(
              onPressed: busy || recording ? null : () => changeItem(null),
              icon: const Icon(Icons.add),
              label: const Text('Добавить продукт'),
            ),
          if ((items.isNotEmpty || pending != null) && pendingQueue == null)
            Padding(
              padding: const EdgeInsets.only(top: 16),
              child: FilledButton.icon(
                onPressed: busy || recording ? null : save,
                icon: const Icon(Icons.check),
                label: Text(
                  !locked ? 'Сохранить в дневник' : 'Повторить сохранение',
                ),
              ),
            ),
          if (pendingQueue != null)
            Padding(
              padding: const EdgeInsets.only(top: 16),
              child: FilledButton.icon(
                onPressed: busy || recording ? null : () => enqueue(),
                icon: const Icon(Icons.playlist_add),
                label: const Text('Повторить отправку в очередь'),
              ),
            ),
        ],
      ),
    ),
  );
}

class QueuePage extends StatefulWidget {
  const QueuePage({super.key, required this.api});
  final Api api;
  @override
  State<QueuePage> createState() => _QueuePageState();
}

class _QueuePageState extends State<QueuePage> {
  Map<String, dynamic>? pendingAnalysis;
  List<Map<String, dynamic>> items = [];
  bool loading = true;
  String? error;
  @override
  void initState() {
    super.initState();
    refresh();
  }

  Future<void> refresh() async {
    setState(() {
      loading = true;
      error = null;
    });
    try {
      final result = await widget.api.request('GET', '/queue');
      if (mounted) {
        setState(() {
          items = (result['items'] as List)
              .cast<Map<String, dynamic>>()
              .where(
                (item) => ['queued', 'processing'].contains(item['status']),
              )
              .toList();
        });
      }
    } catch (e) {
      if (mounted) setState(() => error = errorText(e));
    } finally {
      if (mounted) setState(() => loading = false);
    }
  }

  Future<void> analyze() async {
    final ids = items
        .where((i) => i['status'] == 'queued')
        .take(30)
        .map((i) => i['id'])
        .toList();
    if (ids.isEmpty && pendingAnalysis == null) return;
    pendingAnalysis ??= {'request_id': uuid(), 'entry_ids': ids};
    setState(() {
      loading = true;
      error = null;
    });
    try {
      final job = await widget.api.request(
        'POST',
        '/queue/parse',
        body: pendingAnalysis,
      );
      pendingAnalysis = null;
      if (!mounted) return;
      await Navigator.push(
        context,
        MaterialPageRoute<String>(
          builder: (_) => GrokJobPage(
            api: widget.api,
            jobId: job['id'] as String,
            initial: job,
          ),
        ),
      );
      if (mounted) await refresh();
    } catch (e) {
      if (mounted) {
        setState(() {
          error = errorText(e);
          loading = false;
        });
      }
    }
  }

  Future<void> remove(Map<String, dynamic> item) async {
    final yes = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Убрать из очереди?'),
        content: SingleChildScrollView(child: Text(item['text'])),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Отмена'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Убрать'),
          ),
        ],
      ),
    );
    if (yes != true || !mounted) return;
    setState(() => loading = true);
    try {
      await widget.api.request('DELETE', '/queue/${item['id']}');
      await refresh();
    } catch (e) {
      if (mounted) {
        setState(() {
          error = errorText(e);
          loading = false;
        });
      }
    }
  }

  Future<void> openCurrentJob(String jobId) async {
    await Navigator.push(
      context,
      MaterialPageRoute<String>(
        builder: (_) => GrokJobPage(api: widget.api, jobId: jobId),
      ),
    );
    if (mounted) await refresh();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Очередь на разбор')),
    body: RefreshIndicator(
      onRefresh: refresh,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.all(20),
        children: [
          const Text(
            'Grok разберёт накопленные тексты и фотографии вместе. Результаты сохранятся в дневнике, даже если закрыть приложение.',
            style: TextStyle(color: Colors.black54),
          ),
          const SizedBox(height: 16),
          FilledButton.icon(
            onPressed:
                loading ||
                    (pendingAnalysis == null &&
                        !items.any((i) => i['status'] == 'queued'))
                ? null
                : analyze,
            icon: const Icon(Icons.auto_awesome),
            label: const Text('Разобрать очередь'),
          ),
          if (items.where((i) => i['status'] == 'queued').length > 30)
            const Text('За один раз разбираем первые 30 записей.'),
          if (loading)
            const Center(child: CircularProgressIndicator())
          else if (error != null) ...[
            Text(error!, style: const TextStyle(color: Colors.red)),
            TextButton(onPressed: refresh, child: const Text('Повторить')),
          ] else if (items.isEmpty)
            const Text('Очередь пока пуста')
          else
            ...items.map(
              (item) => Card(
                color: Colors.white,
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '${item['entry_date']} · ${meals[item['meal']] ?? 'Перекус'}',
                        style: const TextStyle(
                          color: Colors.black54,
                          fontSize: 12,
                        ),
                      ),
                      const SizedBox(height: 8),
                      SelectableText(
                        item['text'].toString().isEmpty
                            ? 'Фото еды'
                            : item['text'],
                      ),
                      if (item['has_image'] == true)
                        const Text(
                          'Прикреплена картинка',
                          style: TextStyle(color: Colors.black54),
                        ),
                      if (item['status'] == 'processing') ...[
                        const Padding(
                          padding: EdgeInsets.only(top: 8),
                          child: Text('Передано на разбор'),
                        ),
                        if (item['job_id'] is String)
                          Align(
                            alignment: Alignment.centerRight,
                            child: TextButton.icon(
                              onPressed: () =>
                                  openCurrentJob(item['job_id'] as String),
                              icon: const Icon(Icons.hourglass_top),
                              label: const Text('Проверить разбор'),
                            ),
                          ),
                      ] else
                        Align(
                          alignment: Alignment.centerRight,
                          child: TextButton.icon(
                            onPressed: () => remove(item),
                            icon: const Icon(Icons.delete_outline),
                            label: const Text('Убрать'),
                          ),
                        ),
                    ],
                  ),
                ),
              ),
            ),
        ],
      ),
    ),
  );
}

String jobStatus(dynamic status) => switch (status) {
  'completed' => 'Разбор сохранён в дневнике',
  'failed' => 'Не удалось завершить разбор',
  'dispatch_unknown' => 'Ожидаем ответ Grok',
  _ => 'Grok разбирает записи',
};

class GrokJobPage extends StatefulWidget {
  const GrokJobPage({
    super.key,
    required this.api,
    required this.jobId,
    this.initial,
  });
  final Api api;
  final String jobId;
  final Map<String, dynamic>? initial;
  @override
  State<GrokJobPage> createState() => _GrokJobPageState();
}

class _GrokJobPageState extends State<GrokJobPage> {
  Map<String, dynamic>? job;
  Timer? timer;
  String? error;
  bool checking = false;
  bool get finished => ['completed', 'failed'].contains(job?['status']);
  @override
  void initState() {
    super.initState();
    job = widget.initial;
    refresh();
  }

  Future<void> refresh() async {
    if (checking) return;
    timer?.cancel();
    checking = true;
    try {
      final value = await widget.api.request(
        'GET',
        '/queue/jobs/${widget.jobId}',
      );
      if (mounted) {
        setState(() {
          job = value;
          error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => error = errorText(e));
    } finally {
      checking = false;
      if (mounted && !finished) {
        timer = Timer(const Duration(seconds: 5), refresh);
      }
    }
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('Разбор еды'),
      actions: [
        IconButton(onPressed: refresh, icon: const Icon(Icons.refresh)),
      ],
    ),
    body: ListView(
      padding: const EdgeInsets.all(20),
      children: [
        Text(
          jobStatus(job?['status']),
          style: Theme.of(context).textTheme.titleLarge,
        ),
        const SizedBox(height: 16),
        if (!finished) ...[
          const Center(child: CircularProgressIndicator()),
          const SizedBox(height: 16),
          const Text(
            'Можно закрыть этот экран. Результат появится в дневнике.',
          ),
        ],
        if (error != null)
          Text(error!, style: const TextStyle(color: Colors.red)),
        if (job?['error'] != null)
          Text(job!['error'], style: const TextStyle(color: Colors.red)),
        if (job?['status'] == 'failed')
          const Text(
            'Исходные записи остались в очереди. Их можно разобрать ещё раз.',
          ),
        if (job?['status'] == 'completed') ...[
          AnalysisTables(
            nutrition: (job!['nutrition'] as List).cast<Map<String, dynamic>>(),
          ),
          for (final note in job!['skipped'] as List? ?? [])
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text('Пропущено: $note'),
            ),
        ],
        const SizedBox(height: 20),
        OutlinedButton(
          onPressed: () => Navigator.pop(
            context,
            finished ? 'Разбор завершён' : 'Разбор продолжается',
          ),
          child: const Text('Вернуться к дневнику'),
        ),
      ],
    ),
  );
}

class DailyMacros extends StatelessWidget {
  const DailyMacros({super.key, required this.diary});
  final Map<String, dynamic>? diary;

  @override
  Widget build(BuildContext context) {
    const labels = {'protein': 'Белки', 'fat': 'Жиры', 'carbs': 'Углеводы'};
    final missing = diary?['missing_macros'] as Map? ?? {};
    final estimated = diary?['estimated_macros'] as Map? ?? {};
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Wrap(
          spacing: 18,
          runSpacing: 8,
          children: labels.entries.map((entry) {
            final value = diary?['total_${entry.key}'];
            final prefix = value != null && estimated[entry.key] == true
                ? '≈ '
                : '';
            return Text(
              '${entry.value}: $prefix${number(value)}${value == null ? '' : ' г'}',
              style: const TextStyle(
                color: Colors.white,
                fontSize: 17,
                fontWeight: FontWeight.w600,
              ),
            );
          }).toList(),
        ),
        if (missing.values.any((value) => (value as num) > 0))
          const Padding(
            padding: EdgeInsets.only(top: 8),
            child: Text(
              'БЖУ рассчитаны не для всех продуктов',
              style: TextStyle(color: Color(0xFFCEE0D6), fontSize: 12),
            ),
          ),
      ],
    );
  }
}

class AnalysisTables extends StatelessWidget {
  const AnalysisTables({
    super.key,
    required this.nutrition,
    this.onEdit,
    this.onDelete,
  });
  final List<Map<String, dynamic>> nutrition;
  final void Function(Map<String, dynamic>)? onEdit;
  final void Function(Map<String, dynamic>)? onDelete;

  String macros(Map<String, dynamic> item) {
    final values = [
      'protein',
      'fat',
      'carbs',
    ].map((key) => number(item[key])).join('/');
    final estimated =
        item['macros_source'] == 'estimate' ||
        item['portion_is_estimate'] == true;
    final hasValues = [
      'protein',
      'fat',
      'carbs',
    ].any((key) => item[key] != null);
    return '${estimated && hasValues ? '≈ ' : ''}$values';
  }

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      const SizedBox(height: 16),
      Text('Что съедено', style: Theme.of(context).textTheme.titleLarge),
      const Text(
        'Б/Ж/У — белки / жиры / углеводы, г на порцию.',
        style: TextStyle(color: Colors.black54, fontSize: 12),
      ),
      for (var index = 0; index < nutrition.length; index++) ...[
        if (index > 0) const Divider(height: 1),
        foodRow(context, nutrition[index]),
      ],
      const SizedBox(height: 20),
    ],
  );

  Widget foodRow(BuildContext context, Map<String, dynamic> item) {
    final portionEstimated = item['portion_is_estimate'] == true;
    final caloriesEstimated = item['nutrition_source'] == 'estimate';
    final unit = ['г', 'мл'].contains(item['unit']) ? item['unit'] : null;
    String value(dynamic amount, {bool estimated = false}) =>
        '${amount != null && estimated ? '≈ ' : ''}${number(amount)}';
    final metrics = [
      (
        label: 'Порция',
        value: item['quantity'] == null
            ? '—'
            : '${value(item['quantity'], estimated: portionEstimated)}${unit == null ? '' : ' $unit'}',
      ),
      (
        label: 'Ккал / 100${unit == null ? '' : ' $unit'}',
        value: value(item['kcal_per_100g'], estimated: caloriesEstimated),
      ),
      (
        label: 'Ккал итог',
        value: value(
          item['kcal'],
          estimated: caloriesEstimated || portionEstimated,
        ),
      ),
    ];
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Padding(
                  padding: const EdgeInsets.only(top: 10),
                  child: Text(
                    item['name'],
                    style: const TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
              ),
              if (onEdit != null)
                IconButton(
                  tooltip: 'Изменить продукт',
                  icon: const Icon(Icons.edit_outlined),
                  onPressed: () => onEdit!(item),
                ),
              if (onDelete != null)
                IconButton(
                  tooltip: 'Удалить продукт',
                  icon: const Icon(Icons.delete_outline),
                  onPressed: () => onDelete!(item),
                ),
            ],
          ),
          const SizedBox(height: 10),
          LayoutBuilder(
            builder: (context, constraints) {
              final scale = MediaQuery.textScalerOf(context).scale(16) / 16;
              final columns = (constraints.maxWidth / (96 * scale))
                  .floor()
                  .clamp(1, 3);
              final width =
                  (constraints.maxWidth - (columns - 1) * 12) / columns;
              return Wrap(
                spacing: 12,
                runSpacing: 12,
                children: metrics.map((metric) {
                  final align = columns == 3 && metric == metrics.last
                      ? CrossAxisAlignment.end
                      : CrossAxisAlignment.start;
                  return SizedBox(
                    width: width,
                    child: Column(
                      crossAxisAlignment: align,
                      children: [
                        Text(
                          metric.label,
                          style: const TextStyle(
                            fontSize: 12,
                            color: Colors.black54,
                          ),
                        ),
                        const SizedBox(height: 4),
                        Text(
                          metric.value,
                          style: const TextStyle(
                            fontSize: 16,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ],
                    ),
                  );
                }).toList(),
              );
            },
          ),
          const SizedBox(height: 10),
          Text(
            'Б/Ж/У ${macros(item)} г',
            style: const TextStyle(fontSize: 14, color: Colors.black54),
          ),
        ],
      ),
    );
  }
}

Future<Map<String, dynamic>?> itemDialog(
  BuildContext context,
  Map<String, dynamic> original, {
  bool barcodeReview = false,
}) async {
  final form = GlobalKey<FormState>();
  final name = TextEditingController(text: original['name']);
  final quantity = TextEditingController(text: '${original['quantity'] ?? ''}');
  final calories = TextEditingController(
    text: '${original['kcal_per_100g'] ?? ''}',
  );
  final macros = {
    for (final field in ['protein_per_100g', 'fat_per_100g', 'carbs_per_100g'])
      field: TextEditingController(text: '${original[field] ?? ''}'),
  };
  const macroLabels = {
    'protein_per_100g': 'Белки, г',
    'fat_per_100g': 'Жиры, г',
    'carbs_per_100g': 'Углеводы, г',
  };
  final fieldStyle = Theme.of(context).textTheme.bodyLarge!
      .copyWith(fontSize: 16);
  const labelStyle = TextStyle(fontSize: 14);
  String unit = ['г', 'мл'].contains(original['unit']) ? original['unit'] : 'г';
  String source =
      ['estimate', 'label', 'manual'].contains(original['nutrition_source'])
      ? original['nutrition_source']
      : 'manual';
  bool verified = original['verified'] == true;
  String normalized(String s) => s.trim().replaceAll(',', '.');
  String? numeric(
    String? v, {
    bool optional = false,
    double max = 100000,
    int places = 3,
  }) {
    if ((v ?? '').trim().isEmpty && optional) return null;
    final value = normalized(v ?? '');
    final n = double.tryParse(value);
    if (n == null ||
        !n.isFinite ||
        n < (optional ? 0 : 0.001) ||
        n > max ||
        !RegExp('^\\d+(\\.\\d{1,$places})?\$').hasMatch(value)) {
      return optional
          ? 'От 0 до $max, до $places знаков'
          : 'Укажите положительное число, до $places знаков';
    }
    return null;
  }

  return showDialog<Map<String, dynamic>>(
    context: context,
    builder: (ctx) => StatefulBuilder(
      builder: (ctx, change) => AlertDialog(
        title: const Text('Продукт', style: TextStyle(fontSize: 22)),
        content: SingleChildScrollView(
          child: Form(
            key: form,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                TextFormField(
                  controller: name,
                  onChanged: barcodeReview
                      ? (_) => change(() => verified = false)
                      : null,
                  style: fieldStyle,
                  maxLength: 255,
                  decoration: const InputDecoration(
                    labelText: 'Название',
                    labelStyle: labelStyle,
                  ),
                  validator: (v) =>
                      (v ?? '').trim().isEmpty ? 'Укажите название' : null,
                ),
                const SizedBox(height: 10),
                TextFormField(
                  controller: quantity,
                  style: fieldStyle,
                  keyboardType: const TextInputType.numberWithOptions(
                    decimal: true,
                  ),
                  decoration: const InputDecoration(
                    labelText: 'Количество',
                    labelStyle: labelStyle,
                  ),
                  validator: (v) => numeric(v),
                ),
                const SizedBox(height: 10),
                DropdownButtonFormField<String>(
                  initialValue: unit,
                  isExpanded: true,
                  style: fieldStyle.copyWith(
                    color: Theme.of(ctx).colorScheme.onSurface,
                  ),
                  items: ['г', 'мл']
                      .map(
                        (v) => DropdownMenuItem(
                          value: v,
                          child: Text(
                            v,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                      )
                      .toList(),
                  onChanged: (v) => change(() {
                    unit = v!;
                    if (barcodeReview) verified = false;
                  }),
                  decoration: const InputDecoration(
                    labelText: 'Единица',
                    labelStyle: labelStyle,
                  ),
                ),
                const SizedBox(height: 10),
                TextFormField(
                  controller: calories,
                  onChanged: barcodeReview
                      ? (_) => change(() => verified = false)
                      : null,
                  style: fieldStyle,
                  keyboardType: const TextInputType.numberWithOptions(
                    decimal: true,
                  ),
                  decoration: InputDecoration(
                    labelText: 'Ккал на 100 $unit',
                    labelStyle: labelStyle,
                  ),
                  validator: (v) =>
                      numeric(v, optional: true, max: 2000, places: 2),
                ),
                const SizedBox(height: 10),
                Align(
                  alignment: Alignment.centerLeft,
                  child: Text('БЖУ на 100 $unit', style: labelStyle),
                ),
                for (final field in macros.keys) ...[
                  const SizedBox(height: 10),
                  TextFormField(
                    key: ValueKey(field),
                    controller: macros[field],
                    onChanged: barcodeReview
                        ? (_) => change(() => verified = false)
                        : null,
                    style: fieldStyle,
                    keyboardType: const TextInputType.numberWithOptions(
                      decimal: true,
                    ),
                    decoration: InputDecoration(
                      labelText: macroLabels[field],
                      labelStyle: labelStyle,
                    ),
                    validator: (v) =>
                        numeric(v, optional: true, max: 200, places: 2),
                  ),
                ],
                const SizedBox(height: 10),
                if (!barcodeReview)
                  DropdownButtonFormField<String>(
                    initialValue: source,
                    isExpanded: true,
                    style: fieldStyle.copyWith(
                      color: Theme.of(ctx).colorScheme.onSurface,
                    ),
                    decoration: const InputDecoration(),
                    items: ['estimate', 'label', 'manual']
                        .map(
                          (v) => DropdownMenuItem(
                            value: v,
                            child: Text(
                              sourceLabel(v),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                        )
                        .toList(),
                    onChanged: (v) => change(() => source = v!),
                  ),
                if (barcodeReview)
                  CheckboxListTile(
                    contentPadding: EdgeInsets.zero,
                    value: verified,
                    title: const Text(
                      'Значения сверены с упаковкой',
                      style: TextStyle(fontSize: 14),
                    ),
                    onChanged: (value) =>
                        change(() => verified = value ?? false),
                  ),
                if (!barcodeReview && source == 'label')
                  const Padding(
                    padding: EdgeInsets.only(top: 12),
                    child: Text(
                      'Укажите значение с упаковки именно этого продукта. Дневник запомнит его для такого же названия и единицы.',
                    ),
                  ),
              ],
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Отмена'),
          ),
          FilledButton(
            onPressed: () {
              if (!form.currentState!.validate()) return;
              if (!barcodeReview &&
                  source == 'label' &&
                  calories.text.trim().isEmpty) {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(
                    content: Text('Для этикетки укажите калорийность'),
                  ),
                );
                return;
              }
              final macrosChanged = macros.entries.any(
                (entry) =>
                    double.tryParse(normalized(entry.value.text)) !=
                    double.tryParse('${original[entry.key] ?? ''}'),
              );
              Navigator.pop(ctx, {
                'name': name.text.trim(),
                'quantity': normalized(quantity.text),
                'unit': unit,
                'kcal_per_100g': calories.text.trim().isEmpty
                    ? null
                    : normalized(calories.text),
                'nutrition_source': barcodeReview
                    ? (verified ? 'label' : 'estimate')
                    : source,
                if (barcodeReview) 'barcode_verified': verified,
                if (original.containsKey('barcode'))
                  'barcode':
                      barcodeReview ||
                          (name.text.trim() == original['name'] &&
                              unit == original['unit'])
                      ? original['barcode']
                      : null,
                if (original.containsKey('library_ref'))
                  'library_ref':
                      name.text.trim() == original['name'] &&
                          unit == original['unit'] &&
                          !macrosChanged &&
                          double.tryParse(normalized(calories.text)) ==
                              double.tryParse(
                                '${original['kcal_per_100g'] ?? ''}',
                              )
                      ? original['library_ref']
                      : null,
                for (final entry in macros.entries)
                  entry.key: entry.value.text.trim().isEmpty
                      ? null
                      : normalized(entry.value.text),
                'macros_source': barcodeReview
                    ? (verified ? 'label' : 'estimate')
                    : macrosChanged
                    ? source
                    : original['macros_source'] ?? source,
              });
            },
            child: const Text('Готово'),
          ),
        ],
      ),
    ),
  );
}
