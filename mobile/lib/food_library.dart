part of 'main.dart';

const foodNutrients = [
  'kcal_per_100g',
  'protein_per_100g',
  'fat_per_100g',
  'carbs_per_100g',
];
String foodSearchKey(String text) =>
    text.toLowerCase().replaceAll('ё', 'е').replaceAll(',', '.').trim();
double? foodValue(dynamic v) => double.tryParse('$v'.replaceAll(',', '.'));
String foodDecimal(num value) => value.toStringAsFixed(2);

class FoodLibraryEntry {
  FoodLibraryEntry({
    required this.id,
    required this.kind,
    required this.revision,
    required this.data,
  });
  final String id, kind;
  final int revision;
  final Map<String, dynamic> data;
  String get name => data['name'] as String;
  Map<String, dynamic> get reference => {
    'kind': kind,
    'id': id,
    'revision': revision,
  };
  Map<String, dynamic> get json => {
    'id': id,
    'kind': kind,
    'revision': revision,
    'data': data,
  };
  factory FoodLibraryEntry.fromJson(Map<String, dynamic> value) {
    final data = Map<String, dynamic>.from(value['data'] as Map);
    if (value['id'] is! String ||
        !['catalog', 'product', 'recipe'].contains(value['kind']) ||
        value['revision'] is! int ||
        (value['revision'] as int) < 1 ||
        data['name'] is! String ||
        (data['name'] as String).trim().isEmpty ||
        data['unit'] != 'г' ||
        foodNutrients.any(
          (k) =>
              foodValue(data[k]) == null ||
              !foodValue(data[k])!.isFinite ||
              foodValue(data[k])! < 0 ||
              foodValue(data[k])! > (k == 'kcal_per_100g' ? 2000 : 200),
        )) {
      throw const FormatException('Некорректные данные справочника');
    }
    return FoodLibraryEntry(
      id: value['id'],
      kind: value['kind'],
      revision: value['revision'],
      data: data,
    );
  }
  Map<String, dynamic> portion(String grams) => {
    'name': name,
    'quantity': grams,
    'unit': 'г',
    for (final k in foodNutrients) k: data[k],
    'nutrition_source': 'estimate',
    'macros_source': 'estimate',
    'library_ref': reference,
  };
}

Map<String, dynamic> decodeFoodCatalog(String raw) {
  final value = jsonDecode(raw) as Map<String, dynamic>;
  final products = value['products'] as List;
  if (value['version'] is! int ||
      value['version'] < 1 ||
      products.isEmpty ||
      products.length > 10000) {
    throw const FormatException('Некорректный справочник');
  }
  final ids = <String>{};
  for (final product in products) {
    final entry = FoodLibraryEntry.fromJson({
      'id': product['id'],
      'kind': 'catalog',
      'revision': value['version'],
      'data': product,
    });
    if (!ids.add(entry.id)) throw const FormatException('Повтор продукта');
  }
  return value;
}

int _foodDistance(String a, String b) {
  if ((a.length - b.length).abs() > 2) return 3;
  var previous = List.generate(b.length + 1, (i) => i);
  for (var i = 1; i <= a.length; i++) {
    final row = [i];
    for (var j = 1; j <= b.length; j++) {
      row.add(
        min(
          row[j - 1] + 1,
          min(
            previous[j] + 1,
            previous[j - 1] + (a[i - 1] == b[j - 1] ? 0 : 1),
          ),
        ),
      );
    }
    previous = row;
  }
  return previous.last;
}

int foodMatch(FoodLibraryEntry entry, String query) {
  final q = foodSearchKey(query), name = foodSearchKey(entry.name);
  if (q.isEmpty || name == q) return 0;
  if (name.startsWith(q)) return 1;
  if (name.contains(q)) return 2;
  final words = [
    name,
    ...((entry.data['aliases'] as List?) ?? []).map((v) => foodSearchKey('$v')),
  ];
  if (words.any((v) => v.contains(q))) return 3;
  if (q.length >= 4 &&
      q.length <= 40 &&
      words.any(
        (v) => v
            .split(' ')
            .any((w) => _foodDistance(w, q) <= (q.length >= 7 ? 2 : 1)),
      )) {
    return 4;
  }
  return 100;
}

List<FoodLibraryEntry> findFoods(List<FoodLibraryEntry> entries, String query) {
  final scores = <String, int>{
    for (final e in entries) e.id: foodMatch(e, query),
  };
  return entries.where((e) => scores[e.id]! < 100).toList()..sort((a, b) {
    final score = scores[a.id]!.compareTo(scores[b.id]!);
    return score != 0 ? score : a.name.compareTo(b.name);
  });
}

Map<String, dynamic> recipeNutrition(
  List<Map<String, dynamic>> ingredients,
  double yieldGrams,
) {
  if (!yieldGrams.isFinite || yieldGrams <= 0 || ingredients.isEmpty) {
    throw const FormatException('Укажите ингредиенты и вес готового блюда');
  }
  return {
    for (final k in foodNutrients)
      k: foodDecimal(
        ingredients.fold<double>(
              0,
              (sum, row) =>
                  sum + foodValue(row[k])! * foodValue(row['quantity'])! / 100,
            ) *
            100 /
            yieldGrams,
      ),
  };
}

class FoodLibraryRepository {
  FoodLibraryRepository(this.api)
    : owner = api.token,
      origin = isPublicDoor(api.url) ? publicApiBase : api.url;
  final Api api;
  final String owner, origin;
  List<FoodLibraryEntry> products = [], personal = [];
  Map<String, dynamic>? recipeDraft;
  int mutation = 0;
  Future<void> writing = Future.value();
  int catalogVersion = 1;
  static Future<Map<String, dynamic>>? _bundle;
  String get cacheOwner => jsonEncode([origin, owner]);
  void checkOwner() {
    if (api.token != owner ||
        (isPublicDoor(api.url) ? publicApiBase : api.url) != origin) {
      throw Exception('Пользователь изменился. Откройте добавление заново');
    }
  }

  Future<void> load() async {
    final catalog = await (_bundle ??= rootBundle
        .loadString('assets/food_catalog.json')
        .then(
          (raw) => raw.length > 200000
              ? compute(decodeFoodCatalog, raw)
              : Future.value(decodeFoodCatalog(raw)),
        ));
    applyCatalog(catalog);
    try {
      final raw = await device.invokeMethod<String>('loadLibrary', {
        'owner': cacheOwner,
      });
      if (raw != null) {
        final saved = jsonDecode(raw) as Map<String, dynamic>;
        if (saved['owner'] == owner && saved['origin'] == origin) {
          personal = (saved['items'] as List)
              .map(
                (v) => FoodLibraryEntry.fromJson(Map<String, dynamic>.from(v)),
              )
              .toList();
          recipeDraft = saved['recipe_draft'] == null
              ? null
              : Map<String, dynamic>.from(saved['recipe_draft']);
          final cachedCatalog = saved['catalog'];
          if (cachedCatalog != null &&
              cachedCatalog['version'] > catalogVersion &&
              isPublicDoor(origin)) {
            applyCatalog(
              await compute(decodeFoodCatalog, jsonEncode(cachedCatalog)),
            );
          }
        }
      }
    } catch (_) {
      /* The bundled catalogue still works without a local cache. */
    }
  }

  Map<String, dynamic>? downloadedCatalog;
  void applyCatalog(Map<String, dynamic> catalog) {
    catalogVersion = catalog['version'];
    products = (catalog['products'] as List)
        .map(
          (p) => FoodLibraryEntry.fromJson({
            'id': p['id'],
            'kind': 'catalog',
            'revision': catalogVersion,
            'data': p,
          }),
        )
        .toList();
    if (catalogVersion > 1) downloadedCatalog = catalog;
  }

  Future<void> persist() async {
    checkOwner();
    final value = jsonEncode({
      'owner': owner,
      'origin': origin,
      'items': personal.map((e) => e.json).toList(),
      'recipe_draft': recipeDraft,
      if (downloadedCatalog != null) 'catalog': downloadedCatalog,
    });
    writing = writing.catchError((_) {}).then((_) async {
      checkOwner();
      await device.invokeMethod('storeLibrary', {
        'owner': cacheOwner,
        'value': value,
      });
    });
    await writing;
  }

  Future<void> sync({bool Function()? stillActive}) async {
    checkOwner();
    final startedAt = mutation;
    final result = await api.request('GET', '/library');
    checkOwner();
    if (startedAt != mutation || (stillActive != null && !stillActive())) {
      return;
    }
    final fetched = (result['items'] as List)
        .map((p) => FoodLibraryEntry.fromJson(Map<String, dynamic>.from(p)))
        .toList();
    personal = fetched;
    await persist();
    if (isPublicDoor(origin) &&
        result['catalog_version'] is int &&
        result['catalog_version'] > catalogVersion) {
      final updated = await api.request('GET', '/catalog');
      checkOwner();
      final checked = await compute(decodeFoodCatalog, jsonEncode(updated));
      if (checked['version'] > catalogVersion) {
        applyCatalog(checked);
        await persist();
      }
    }
  }

  Future<FoodLibraryEntry> save(String id, Map<String, dynamic> body) async {
    checkOwner();
    final response = await api.request('PUT', '/library/$id', body: body);
    checkOwner();
    final entry = FoodLibraryEntry.fromJson(response);
    mutation++;
    personal.removeWhere((e) => e.id == id);
    personal.add(entry);
    // Once the server confirms, a cache failure must not duplicate the save.
    try {
      await persist();
    } catch (_) {}
    return entry;
  }

  Future<void> delete(FoodLibraryEntry entry, String requestId) async {
    checkOwner();
    await api.request(
      'DELETE',
      '/library/${entry.id}',
      body: {'request_id': requestId, 'revision': entry.revision},
    );
    checkOwner();
    mutation++;
    personal.removeWhere((e) => e.id == entry.id);
    try {
      await persist();
    } catch (_) {}
  }
}

class FoodPickerPage extends StatefulWidget {
  const FoodPickerPage({
    super.key,
    required this.api,
    this.repository,
    this.ingredient = false,
  });
  final Api api;
  final FoodLibraryRepository? repository;
  final bool ingredient;
  @override
  State<FoodPickerPage> createState() => _FoodPickerPageState();
}

class _FoodPickerPageState extends State<FoodPickerPage> {
  late final repo = widget.repository ?? FoodLibraryRepository(widget.api);
  final search = TextEditingController();
  bool loading = true, syncing = false;
  String category = 'Все', query = '';
  String? warning;
  final deleteRequests = <String, String>{};
  @override
  void initState() {
    super.initState();
    load();
  }

  @override
  void dispose() {
    search.dispose();
    super.dispose();
  }

  Future<void> load() async {
    try {
      if (widget.repository == null) await repo.load();
      if (!mounted) return;
      setState(() => loading = false);
      if (widget.repository == null) await sync();
    } catch (e) {
      if (mounted) {
        setState(() {
          loading = false;
          warning = errorText(e);
        });
      }
    }
  }

  Future<void> sync() async {
    if (syncing) return;
    setState(() => syncing = true);
    try {
      await repo.sync(stillActive: () => mounted);
      if (mounted) setState(() => warning = null);
    } catch (_) {
      if (mounted) {
        setState(
          () => warning = 'Сервер недоступен. Продукты и сохранённые рецепты доступны на телефоне.',
        );
      }
    } finally {
      if (mounted) setState(() => syncing = false);
    }
  }

  Future<void> choose(FoodLibraryEntry entry) async {
    final item = await Navigator.push<Map<String, dynamic>>(
      context,
      MaterialPageRoute(builder: (_) => _FoodPortionPage(entry: entry)),
    );
    if (item != null && mounted) Navigator.pop(context, item);
  }

  Future<void> editRecipe([FoodLibraryEntry? entry]) async {
    final draft = repo.recipeDraft;
    if (entry != null && draft != null) {
      if (draft['id'] == entry.id) {
        entry = null;
      } else {
        final replace = await showDialog<bool>(
          context: context,
          builder: (ctx) => AlertDialog(
            title: const Text('Есть черновик рецепта'),
            content: const Text(
              'Продолжить черновик или заменить его другим рецептом?',
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(ctx, false),
                child: const Text('Продолжить'),
              ),
              TextButton(
                onPressed: () => Navigator.pop(ctx, true),
                child: const Text('Заменить'),
              ),
            ],
          ),
        );
        if (!mounted || replace == null) return;
        if (!replace) entry = null;
      }
    }
    await Navigator.push<void>(
      context,
      MaterialPageRoute(
        builder: (_) => RecipeEditorPage(repository: repo, entry: entry),
      ),
    );
    if (mounted) setState(() {});
  }

  Future<void> editProduct([FoodLibraryEntry? entry]) async {
    await Navigator.push<void>(
      context,
      MaterialPageRoute(
        builder: (_) => Scaffold(
          appBar: AppBar(title: const Text('Мой продукт')),
          body: _FoodManualForm(repository: repo, saveOnly: true, entry: entry),
        ),
      ),
    );
    if (mounted) setState(() {});
  }

  Future<void> remove(FoodLibraryEntry entry) async {
    final yes = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Удалить «${entry.name}»?'),
        content: const Text('Записи в дневнике сохранятся.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Отмена'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Удалить'),
          ),
        ],
      ),
    );
    if (yes != true) return;
    final requestId = deleteRequests.putIfAbsent(entry.id, uuid);
    try {
      await repo.delete(entry, requestId);
      deleteRequests.remove(entry.id);
      if (mounted) setState(() {});
    } catch (e) {
      if (mounted) setState(() => warning = errorText(e));
    }
  }

  Widget entryTile(FoodLibraryEntry entry) {
    final key = foodSearchKey(query),
        normalizedName = foodSearchKey(entry.name),
        at = normalizedName.indexOf(key);
    return ListTile(
      contentPadding: const EdgeInsets.symmetric(horizontal: 4),
      title: key.isEmpty || at < 0
          ? Text(entry.name)
          : Text.rich(
              TextSpan(
                children: [
                  TextSpan(text: entry.name.substring(0, at)),
                  TextSpan(
                    text: entry.name.substring(at, at + key.length),
                    style: TextStyle(
                      backgroundColor: Theme.of(context)
                          .colorScheme
                          .primaryContainer,
                    ),
                  ),
                  TextSpan(text: entry.name.substring(at + key.length)),
                ],
              ),
            ),
      subtitle: Text(
        '${entry.kind == 'catalog'
            ? entry.data['category']
            : entry.kind == 'recipe'
            ? 'Мой рецепт'
            : 'Мой продукт'} · ≈ ${number(entry.data['kcal_per_100g'])} ккал / 100 г\nБ/Ж/У ≈ ${foodNutrients.skip(1).map((k) => number(entry.data[k])).join('/')} г',
      ),
      isThreeLine: true,
      onTap: () => choose(entry),
      trailing: entry.kind == 'catalog'
          ? const Icon(Icons.chevron_right)
          : PopupMenuButton<String>(
              tooltip: 'Изменить или удалить',
              onSelected: (v) => v == 'delete'
                  ? remove(entry)
                  : entry.kind == 'recipe'
                  ? editRecipe(entry)
                  : editProduct(entry),
              itemBuilder: (_) => const [
                PopupMenuItem(value: 'edit', child: Text('Изменить')),
                PopupMenuItem(value: 'delete', child: Text('Удалить')),
              ],
            ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final all = [
      ...repo.personal.where((e) => e.kind == 'product'),
      ...repo.products,
    ];
    final categories = [
      'Все',
      'Мои продукты',
      ...repo.products.map((p) => '${p.data['category']}').toSet().toList()
        ..sort(),
    ];
    final rows = findFoods(
      all
          .where(
            (e) =>
                category == 'Все' ||
                (category == 'Мои продукты'
                    ? e.kind == 'product'
                    : e.data['category'] == category),
          )
          .toList(),
      query,
    );
    return DefaultTabController(
      length: widget.ingredient ? 2 : 3,
      child: Scaffold(
        appBar: AppBar(
          title: Text(
            widget.ingredient ? 'Добавить ингредиент' : 'Добавить продукт',
          ),
          actions: [
            IconButton(
              onPressed: syncing ? null : sync,
              icon: const Icon(Icons.sync),
              tooltip: 'Обновить справочник',
            ),
          ],
          bottom: TabBar(
            tabs: [
              const Tab(text: 'Продукты'),
              if (!widget.ingredient) const Tab(text: 'Рецепты'),
              const Tab(text: 'Вручную'),
            ],
          ),
        ),
        body: loading
            ? const Center(child: CircularProgressIndicator())
            : Column(
                children: [
                  if (warning != null)
                    Padding(
                      padding: const EdgeInsets.all(12),
                      child: Text(
                        warning!,
                        style: const TextStyle(fontSize: 13),
                      ),
                    ),
                  if (syncing) const LinearProgressIndicator(),
                  Expanded(
                    child: TabBarView(
                      children: [
                        Column(
                          children: [
                            Padding(
                              padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
                              child: TextField(
                                controller: search,
                                maxLength: 80,
                                decoration: const InputDecoration(
                                  labelText: 'Найти продукт',
                                  hintText: 'Гречка, яблоко…',
                                  prefixIcon: Icon(Icons.search),
                                  counterText: '',
                                ),
                                onChanged: (v) => setState(() => query = v),
                              ),
                            ),
                            Padding(
                              padding: const EdgeInsets.symmetric(
                                horizontal: 16,
                              ),
                              child: DropdownButtonFormField<String>(
                                initialValue: category,
                                isExpanded: true,
                                decoration: const InputDecoration(
                                  labelText: 'Категория',
                                ),
                                items: categories
                                    .map(
                                      (v) => DropdownMenuItem(
                                        value: v,
                                        child: Text(v),
                                      ),
                                    )
                                    .toList(),
                                onChanged: (v) => setState(() => category = v!),
                              ),
                            ),
                            Padding(
                              padding: const EdgeInsets.symmetric(
                                horizontal: 16,
                              ),
                              child: Align(
                                alignment: Alignment.centerLeft,
                                child: TextButton.icon(
                                  onPressed: () => editProduct(),
                                  icon: const Icon(Icons.add),
                                  label: const Text('Создать свой продукт'),
                                ),
                              ),
                            ),
                            Expanded(
                              child: rows.isEmpty
                                  ? const Center(
                                      child: Text(
                                        'Не найдено. Добавьте свой продукт\nили введите вручную.',
                                        textAlign: TextAlign.center,
                                      ),
                                    )
                                  : ListView.builder(
                                      padding: const EdgeInsets.symmetric(
                                        horizontal: 16,
                                      ),
                                      itemCount: rows.length,
                                      itemBuilder: (_, i) => entryTile(rows[i]),
                                    ),
                            ),
                          ],
                        ),
                        if (!widget.ingredient)
                          ListView(
                            padding: const EdgeInsets.all(16),
                            children: [
                              FilledButton.icon(
                                onPressed: () => editRecipe(),
                                icon: const Icon(Icons.add),
                                label: Text(
                                  repo.recipeDraft == null
                                      ? 'Создать рецепт'
                                      : 'Продолжить рецепт',
                                ),
                              ),
                              if (repo.personal
                                  .where((e) => e.kind == 'recipe')
                                  .isEmpty)
                                const Padding(
                                  padding: EdgeInsets.symmetric(vertical: 24),
                                  child: Text(
                                    'Сохраните состав и вес готового блюда один раз. Затем добавляйте только съеденную порцию.',
                                  ),
                                ),
                              ...repo.personal
                                  .where((e) => e.kind == 'recipe')
                                  .map(entryTile),
                            ],
                          ),
                        _FoodManualForm(
                          repository: repo,
                          ingredient: widget.ingredient,
                        ),
                      ],
                    ),
                  ),
                ],
              ),
      ),
    );
  }
}

class _FoodPortionPage extends StatefulWidget {
  const _FoodPortionPage({required this.entry});
  final FoodLibraryEntry entry;
  @override
  State<_FoodPortionPage> createState() => _FoodPortionPageState();
}

class _FoodPortionPageState extends State<_FoodPortionPage> {
  final quantity = TextEditingController();
  final form = GlobalKey<FormState>();
  @override
  void dispose() {
    quantity.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final grams = foodValue(quantity.text) ?? 0, entry = widget.entry;
    return Scaffold(
      appBar: AppBar(title: Text(entry.name)),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          Text(
            entry.kind == 'recipe'
                ? 'Мой рецепт · готовое блюдо'
                : entry.data['note'] ?? 'Мой продукт',
          ),
          const SizedBox(height: 16),
          Form(
            key: form,
            child: TextFormField(
              key: const ValueKey('food_portion'),
              controller: quantity,
              keyboardType: const TextInputType.numberWithOptions(
                decimal: true,
              ),
              decoration: const InputDecoration(
                labelText: 'Съеденная порция, г',
              ),
              validator: (v) => foodNumberError(v, positive: true),
              onChanged: (_) => setState(() {}),
            ),
          ),
          const SizedBox(height: 16),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(20),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'На 100 г: ≈ ${number(entry.data['kcal_per_100g'])} ккал',
                  ),
                  Text(
                    'Б/Ж/У ≈ ${foodNutrients.skip(1).map((k) => number(entry.data[k])).join('/')} г',
                  ),
                  const Divider(height: 28),
                  Text(
                    'В порции: ≈ ${number(foodValue(entry.data['kcal_per_100g'])! * grams / 100)} ккал',
                    style: Theme.of(context).textTheme.titleLarge,
                  ),
                  Text(
                    'Б/Ж/У ≈ ${foodNutrients.skip(1).map((k) => number(foodValue(entry.data[k])! * grams / 100)).join('/')} г',
                  ),
                ],
              ),
            ),
          ),
          FilledButton(
            onPressed: () {
              if (form.currentState!.validate()) {
                Navigator.pop(
                  context,
                  entry.portion(quantity.text.replaceAll(',', '.').trim()),
                );
              }
            },
            child: const Text('Добавить порцию'),
          ),
        ],
      ),
    );
  }
}

String? foodNumberError(
  String? text, {
  bool positive = false,
  bool optional = false,
  double max = 100000,
  int places = 3,
}) {
  final s = (text ?? '').trim().replaceAll(',', '.');
  if (optional && s.isEmpty) return null;
  final value = double.tryParse(s);
  if (value == null ||
      !value.isFinite ||
      value < (positive ? 0.001 : 0) ||
      value > max ||
      !RegExp('^\\d+(\\.\\d{1,$places})?\$').hasMatch(s)) {
    return positive
        ? 'Укажите положительное число, до $places знаков'
        : 'От 0 до $max, до $places знаков';
  }
  return null;
}

class _FoodManualForm extends StatefulWidget {
  const _FoodManualForm({
    required this.repository,
    this.ingredient = false,
    this.saveOnly = false,
    this.entry,
  });
  final FoodLibraryRepository repository;
  final bool ingredient, saveOnly;
  final FoodLibraryEntry? entry;
  @override
  State<_FoodManualForm> createState() => _FoodManualFormState();
}

class _FoodManualFormState extends State<_FoodManualForm>
    with AutomaticKeepAliveClientMixin {
  final form = GlobalKey<FormState>();
  late final name = TextEditingController(text: widget.entry?.name ?? '');
  final quantity = TextEditingController();
  late final values = {
    for (final k in foodNutrients)
      k: TextEditingController(text: '${widget.entry?.data[k] ?? ''}'),
  };
  String unit = 'г';
  String source = 'manual';
  bool remember = false, busy = false;
  String? error;
  late final itemId = widget.entry?.id ?? uuid();
  Map<String, dynamic>? pending;
  @override
  bool get wantKeepAlive => true;
  @override
  void dispose() {
    name.dispose();
    quantity.dispose();
    for (final c in values.values) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> submit() async {
    if (!form.currentState!.validate()) return;
    final item = {
      'name': name.text.trim(),
      if (!widget.saveOnly)
        'quantity': quantity.text.replaceAll(',', '.').trim(),
      'unit': unit,
      for (final k in foodNutrients)
        k: values[k]!.text.trim().isEmpty
            ? null
            : values[k]!.text.replaceAll(',', '.').trim(),
      'nutrition_source': source,
      'macros_source': source,
    };
    if (!widget.saveOnly && !remember) {
      Navigator.pop(context, item);
      return;
    }
    pending ??= {
      'request_id': uuid(),
      'revision': widget.entry?.revision ?? 0,
      'kind': 'product',
      'name': item['name'],
      'product': {
        'name': item['name'],
        for (final k in foodNutrients) k: item[k],
      },
    };
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final entry = await widget.repository.save(itemId, pending!);
      if (mounted) {
        Navigator.pop(
          context,
          widget.saveOnly ? null : {...item, 'library_ref': entry.reference},
        );
      }
    } catch (e) {
      if (mounted) setState(() => error = errorText(e));
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    final requiredValues = widget.saveOnly || remember || widget.ingredient;
    const labels = {
      'kcal_per_100g': 'Ккал',
      'protein_per_100g': 'Белки, г',
      'fat_per_100g': 'Жиры, г',
      'carbs_per_100g': 'Углеводы, г',
    };
    return Form(
      key: form,
      child: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          TextFormField(
            controller: name,
            enabled: !busy && pending == null,
            maxLength: 255,
            decoration: const InputDecoration(labelText: 'Название'),
            validator: (v) =>
                (v ?? '').trim().isEmpty ? 'Укажите название' : null,
          ),
          if (!widget.saveOnly) ...[
            TextFormField(
              controller: quantity,
              enabled: !busy && pending == null,
              keyboardType: const TextInputType.numberWithOptions(
                decimal: true,
              ),
              decoration: const InputDecoration(labelText: 'Количество'),
              validator: (v) => foodNumberError(v, positive: true),
            ),
            const SizedBox(height: 12),
            if (!widget.ingredient)
              DropdownButtonFormField<String>(
                initialValue: unit,
                decoration: const InputDecoration(labelText: 'Единица'),
                items: ['г', 'мл']
                    .map((v) => DropdownMenuItem(value: v, child: Text(v)))
                    .toList(),
                onChanged: busy || pending != null || remember
                    ? null
                    : (v) => setState(() => unit = v!),
              ),
          ],
          const SizedBox(height: 16),
          Text('Калории и БЖУ на 100 $unit'),
          for (final k in foodNutrients)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: TextFormField(
                key: ValueKey(k),
                controller: values[k],
                enabled: !busy && pending == null,
                keyboardType: const TextInputType.numberWithOptions(
                  decimal: true,
                ),
                decoration: InputDecoration(
                  labelText: k == 'kcal_per_100g'
                      ? 'Ккал на 100 $unit'
                      : labels[k],
                ),
                validator: (v) => foodNumberError(
                  v,
                  optional:
                      !requiredValues &&
                      (k != 'kcal_per_100g' || source != 'label'),
                  max: k == 'kcal_per_100g' ? 2000 : 200,
                  places: 2,
                ),
              ),
            ),
          if (!widget.saveOnly)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: DropdownButtonFormField<String>(
                initialValue: source,
                isExpanded: true,
                decoration: const InputDecoration(),
                items: ['estimate', 'label', 'manual']
                    .map(
                      (v) => DropdownMenuItem(
                        value: v,
                        child: Text(sourceLabel(v)),
                      ),
                    )
                    .toList(),
                onChanged: busy || pending != null
                    ? null
                    : (v) => setState(() => source = v!),
              ),
            ),
          if (!widget.saveOnly)
            CheckboxListTile(
              contentPadding: EdgeInsets.zero,
              value: remember,
              title: const Text('Сохранить в моих продуктах'),
              onChanged: busy || pending != null || unit != 'г'
                  ? null
                  : (v) => setState(() => remember = v!),
            ),
          if (requiredValues)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 8),
              child: Text(
                'Укажите все значения. Если нутриента нет, введите 0.',
              ),
            ),
          if (error != null)
            Text(
              error!,
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          if (pending != null && !busy)
            TextButton(
              onPressed: () => setState(() {
                pending = null;
                error = null;
              }),
              child: const Text('Изменить данные'),
            ),
          const SizedBox(height: 16),
          FilledButton(
            onPressed: busy ? null : submit,
            child: Text(
              busy
                  ? 'Сохраняю…'
                  : pending != null
                  ? 'Повторить сохранение'
                  : widget.saveOnly
                  ? 'Сохранить продукт'
                  : 'Готово',
            ),
          ),
        ],
      ),
    );
  }
}

class RecipeEditorPage extends StatefulWidget {
  const RecipeEditorPage({super.key, required this.repository, this.entry});
  final FoodLibraryRepository repository;
  final FoodLibraryEntry? entry;
  @override
  State<RecipeEditorPage> createState() => _RecipeEditorPageState();
}

class _RecipeEditorPageState extends State<RecipeEditorPage> {
  final form = GlobalKey<FormState>();
  final name = TextEditingController(), yieldGrams = TextEditingController();
  List<Map<String, dynamic>> ingredients = [];
  late String id;
  int revision = 0;
  bool busy = false;
  bool saved = false;
  String? error;
  Map<String, dynamic>? pending;
  Timer? saveTimer;
  Future<void> lastPersist = Future.value();
  @override
  void initState() {
    super.initState();
    final draft = widget.repository.recipeDraft;
    final entry = widget.entry;
    final data = entry != null
        ? {...entry.data, 'id': entry.id, 'revision': entry.revision}
        : draft ?? <String, dynamic>{};
    id = data['id'] ?? uuid();
    revision = data['revision'] ?? 0;
    name.text = data['name'] ?? '';
    yieldGrams.text = '${data['yield_g'] ?? ''}';
    ingredients = ((data['ingredients'] as List?) ?? [])
        .map((v) => Map<String, dynamic>.from(v))
        .toList();
    pending = data['pending'] == null
        ? null
        : Map<String, dynamic>.from(data['pending']);
    name.addListener(changed);
    yieldGrams.addListener(changed);
  }

  Map<String, dynamic> draft() => {
    'id': id,
    'revision': revision,
    'name': name.text,
    'yield_g': yieldGrams.text,
    'ingredients': ingredients,
    'pending': pending,
  };
  void changed() {
    if (mounted) setState(() {});
    saveTimer?.cancel();
    saveTimer = Timer(const Duration(milliseconds: 300), () {
      unawaited(persistDraft());
    });
  }

  Future<void> persistDraft() {
    saveTimer?.cancel();
    widget.repository.recipeDraft = draft();
    lastPersist = lastPersist
        .catchError((_) {})
        .then((_) => widget.repository.persist());
    return lastPersist.catchError((Object e) {
      if (mounted) {
        setState(
          () =>
              error = 'Не удалось сохранить черновик рецепта: ${errorText(e)}',
        );
      }
    });
  }

  @override
  void dispose() {
    saveTimer?.cancel();
    name.dispose();
    yieldGrams.dispose();
    super.dispose();
  }

  Future<void> addIngredient() async {
    if (ingredients.length >= 60) return;
    final item = await Navigator.push<Map<String, dynamic>>(
      context,
      MaterialPageRoute(
        builder: (_) => FoodPickerPage(
          api: widget.repository.api,
          repository: widget.repository,
          ingredient: true,
        ),
      ),
    );
    if (item == null || !mounted) return;
    setState(
      () => ingredients.add({
        'name': item['name'],
        'quantity': item['quantity'],
        'unit': 'г',
        for (final k in foodNutrients) k: item[k],
        if (item['library_ref'] != null) 'library_ref': item['library_ref'],
      }),
    );
    await persistDraft();
  }

  Future<void> editIngredient(int index) async {
    final old = ingredients[index];
    final updated = await itemDialog(context, {
      ...old,
      'nutrition_source': 'manual',
    });
    if (updated == null || !mounted) return;
    if (updated['unit'] != 'г' ||
        foodNutrients.any((k) => foodValue(updated[k]) == null)) {
      setState(
        () => error =
            'Для рецепта укажите граммы, калории и все БЖУ ингредиента.',
      );
      return;
    }
    setState(
      () => ingredients[index] = {
        'name': updated['name'],
        'quantity': updated['quantity'],
        'unit': 'г',
        for (final k in foodNutrients) k: updated[k],
        if (updated['library_ref'] != null)
          'library_ref': updated['library_ref'],
      },
    );
    await persistDraft();
  }

  Future<void> save() async {
    if (!form.currentState!.validate()) return;
    if (ingredients.isEmpty) {
      setState(() => error = 'Добавьте ингредиенты');
      return;
    }
    pending ??= {
      'request_id': uuid(),
      'revision': revision,
      'kind': 'recipe',
      'name': name.text.trim(),
      'yield_g': yieldGrams.text.trim().replaceAll(',', '.'),
      'ingredients': ingredients,
    };
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await persistDraft();
      await widget.repository.save(id, pending!);
      await lastPersist.catchError((_) {});
      widget.repository.recipeDraft = null;
      try {
        await widget.repository.persist();
      } catch (_) {}
      saved = true;
      if (mounted) Navigator.pop(context);
    } catch (e) {
      if (mounted) setState(() => error = errorText(e));
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> discard() async {
    final yes = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Удалить черновик рецепта?'),
        content: const Text('Сохранённый рецепт и записи дневника останутся.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Отмена'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Удалить черновик'),
          ),
        ],
      ),
    );
    if (yes != true || !mounted) return;
    saveTimer?.cancel();
    await lastPersist.catchError((_) {});
    widget.repository.recipeDraft = null;
    try {
      await widget.repository.persist();
      saved = true;
      if (mounted) Navigator.pop(context);
    } catch (e) {
      if (mounted) setState(() => error = errorText(e));
    }
  }

  @override
  Widget build(BuildContext context) {
    Map<String, dynamic>? preview;
    final output = foodValue(yieldGrams.text);
    if (output != null &&
        output > 0 &&
        output.isFinite &&
        ingredients.isNotEmpty) {
      preview = recipeNutrition(ingredients, output);
    }
    return PopScope(
      canPop: !busy,
      onPopInvokedWithResult: (didPop, result) {
        if (didPop && !saved) unawaited(persistDraft());
      },
      child: Scaffold(
        appBar: AppBar(
          title: Text(revision == 0 ? 'Создать рецепт' : 'Изменить рецепт'),
          actions: [
            IconButton(
              onPressed: busy ? null : discard,
              tooltip: 'Удалить черновик',
              icon: const Icon(Icons.delete_outline),
            ),
          ],
        ),
        body: Form(
          key: form,
          child: ListView(
            padding: const EdgeInsets.all(20),
            children: [
              TextFormField(
                controller: name,
                enabled: !busy && pending == null,
                maxLength: 255,
                decoration: const InputDecoration(
                  labelText: 'Название рецепта',
                ),
                validator: (v) =>
                    (v ?? '').trim().isEmpty ? 'Укажите название' : null,
              ),
              const Text(
                'Указывайте ингредиенты в том виде, в котором взвесили. В справочнике крупы уже варёные; сухую крупу можно ввести вручную.',
              ),
              const SizedBox(height: 12),
              ...ingredients.asMap().entries.map(
                (row) => ListTile(
                  contentPadding: EdgeInsets.zero,
                  title: Text(row.value['name']),
                  subtitle: Text('${number(row.value['quantity'])} г'),
                  onTap: busy || pending != null
                      ? null
                      : () => editIngredient(row.key),
                  trailing: IconButton(
                    tooltip: 'Убрать ингредиент',
                    onPressed: busy || pending != null
                        ? null
                        : () {
                            setState(() => ingredients.removeAt(row.key));
                            unawaited(persistDraft());
                          },
                    icon: const Icon(Icons.close),
                  ),
                ),
              ),
              TextButton.icon(
                onPressed: busy || pending != null ? null : addIngredient,
                icon: const Icon(Icons.add),
                label: const Text('Добавить ингредиент'),
              ),
              const SizedBox(height: 12),
              TextFormField(
                key: const ValueKey('recipe_yield'),
                controller: yieldGrams,
                enabled: !busy && pending == null,
                keyboardType: const TextInputType.numberWithOptions(
                  decimal: true,
                ),
                decoration: const InputDecoration(
                  labelText: 'Вес готового блюда, г',
                ),
                validator: (v) => foodNumberError(v, positive: true),
              ),
              if (preview != null)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 20),
                  child: Text(
                    'На 100 г: ≈ ${number(preview['kcal_per_100g'])} ккал\nБ/Ж/У ≈ ${foodNutrients.skip(1).map((k) => number(preview![k])).join('/')} г',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
              if (error != null)
                Text(
                  error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              if (pending != null && !busy)
                TextButton(
                  onPressed: () {
                    setState(() {
                      pending = null;
                      error = null;
                    });
                    unawaited(persistDraft());
                  },
                  child: const Text('Изменить данные'),
                ),
              FilledButton(
                onPressed: busy ? null : save,
                child: Text(
                  busy
                      ? 'Сохраняю…'
                      : pending != null
                      ? 'Повторить сохранение'
                      : 'Сохранить рецепт',
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
