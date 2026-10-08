part of 'main.dart';

/// Packaging catalog and label reading are separate from saving a meal.
class BarcodePage extends StatefulWidget {
  const BarcodePage({
    super.key,
    required this.api,
    required this.onDraft,
    this.initial,
  });
  final Api api;
  final Map<String, dynamic>? initial;
  final Future<void> Function(Map<String, dynamic>?) onDraft;
  @override
  State<BarcodePage> createState() => _BarcodePageState();
}

class _BarcodePageState extends State<BarcodePage> {
  late final TextEditingController code;
  Map<String, dynamic>? product;
  Map<String, dynamic>? pendingLabel;
  Map<String, dynamic>? job;
  String? canonical;
  String? imagePath;
  String? imageId;
  String? detail;
  String? error;
  bool busy = false;
  bool polling = false;
  Timer? timer;
  bool get running =>
      pendingLabel != null ||
      ['dispatching', 'running', 'dispatch_unknown'].contains(job?['status']);

  @override
  void initState() {
    super.initState();
    final draft = widget.initial;
    code = TextEditingController(text: draft?['code'] ?? '');
    canonical = draft?['canonical'];
    product = draft?['product'] as Map<String, dynamic>?;
    pendingLabel = draft?['pending_label'] as Map<String, dynamic>?;
    job = draft?['job'] as Map<String, dynamic>?;
    imagePath = draft?['image_path'];
    imageId = draft?['image_id'];
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (running) {
        if (job != null) {
          watch();
          poll();
        }
      } else if (draft == null) {
        scan();
      }
    });
  }

  Future<void> persist() => widget.onDraft({
    'code': code.text,
    'canonical': canonical,
    'product': product,
    'pending_label': pendingLabel,
    'job': job,
    'image_path': imagePath,
    'image_id': imageId,
  });

  String readableError(Object e) => e is PlatformException
      ? e.message ?? 'Не удалось открыть камеру'
      : errorText(e);

  Future<void> scan() async {
    if (busy || running) return;
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final value = await device.invokeMethod<String>('scanBarcode');
      if (value != null && mounted) {
        code.text = value;
        await lookup();
      }
    } catch (e) {
      if (mounted) setState(() => error = readableError(e));
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> lookup() async {
    final digits = code.text.trim();
    setState(() {
      busy = true;
      error = null;
      product = null;
      canonical = null;
      detail = null;
      job = null;
      imagePath = null;
      imageId = null;
    });
    try {
      // Do not retain an old product if a new code fails validation or the connection fails.
      await persist();
      final result = await widget.api.request(
        'GET',
        '/barcodes/products/${Uri.encodeComponent(digits)}',
      );
      if (!mounted) return;
      setState(() {
        product = result['product'] as Map<String, dynamic>?;
        canonical = product?['barcode'] ?? result['barcode'];
        detail = result['detail'];
      });
      await persist();
    } catch (e) {
      if (mounted) setState(() => error = readableError(e));
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> readLabel() async {
    if (canonical == null || busy || running) return;
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final selected = await device.invokeMethod<String>('pickImage');
      if (selected == null || !mounted) return;
      final previous = imagePath;
      setState(() {
        imagePath = selected;
        imageId = null;
        job = null;
      });
      await persist();
      if (previous != null && previous != selected) {
        try {
          await device.invokeMethod('deleteImage', {'path': previous});
        } catch (_) {}
      }
      await startLabel();
    } catch (e) {
      if (mounted) setState(() => error = readableError(e));
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> startLabel() async {
    if (!mounted) return;
    if (imageId == null) {
      if (imagePath == null || !await File(imagePath!).exists()) {
        throw Exception('Фото из черновика недоступно. Выберите его заново');
      }
      final upload = await widget.api.request(
        'POST',
        '/images',
        image: imagePath,
      );
      if (!mounted) return;
      imageId = upload['id'];
      await persist();
    }
    if (!mounted) return;
    // Save the immutable request before sending. A lost response is retried with the same id.
    pendingLabel ??= {
      'request_id': uuid(),
      'barcode': canonical,
      'image_id': imageId,
    };
    await persist();
    if (!mounted) return;
    final result = await widget.api.request(
      'POST',
      '/barcodes/labels',
      body: pendingLabel,
    );
    if (!mounted) return;
    job = result;
    pendingLabel = null;
    acceptProduct(result);
    await persist();
    if (mounted) {
      setState(() {});
      watch();
    }
  }

  void acceptProduct(Map<String, dynamic> result) {
    if (result['status'] == 'completed' && result['product'] != null) {
      product = Map<String, dynamic>.from(result['product']);
      detail = 'Проверьте прочитанные значения по упаковке';
    }
  }

  void watch() {
    timer?.cancel();
    if (running && job != null) {
      timer = Timer.periodic(const Duration(seconds: 5), (_) => poll());
    }
  }

  Future<void> poll() async {
    if (polling || !mounted || job == null || !running) return;
    polling = true;
    try {
      final result = await widget.api.request(
        'GET',
        '/barcodes/labels/${job!['id']}',
      );
      if (!mounted) return;
      job = result;
      acceptProduct(result);
      error = null;
      await persist();
      if (!running) timer?.cancel();
      if (mounted) setState(() {});
    } catch (e) {
      if (mounted) setState(() => error = readableError(e));
    } finally {
      polling = false;
    }
  }

  Future<void> retry() async {
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await startLabel();
    } catch (e) {
      if (mounted) setState(() => error = readableError(e));
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> add() async {
    final item = await itemDialog(context, {
      ...?product,
      'barcode': canonical,
      'quantity': product?['quantity'] ?? '',
      'name': product?['name'] ?? '',
      'unit': product?['unit'] ?? 'г',
    }, barcodeReview: true);
    if (item == null || !mounted) return;
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final values = {
        for (final field in [
          'name',
          'unit',
          'kcal_per_100g',
          'protein_per_100g',
          'fat_per_100g',
          'carbs_per_100g',
        ])
          field: item[field],
        'verified': item.remove('barcode_verified') == true,
      };
      product = {...?product, ...item, 'verified': values['verified']};
      await persist();
      await widget.api.request(
        'PUT',
        '/barcodes/products/$canonical',
        body: values,
      );
      if (mounted) {
        Navigator.of(context).pop({...item, 'label_image_path': imagePath});
      }
    } catch (e) {
      if (mounted) setState(() => error = readableError(e));
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> openLink(String url) async {
    try {
      await device.invokeMethod('openUrl', {'url': url});
    } catch (e) {
      if (mounted) setState(() => error = readableError(e));
    }
  }

  @override
  void dispose() {
    timer?.cancel();
    code.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Продукт по штрихкоду')),
    body: ListView(
      padding: const EdgeInsets.all(20),
      children: [
        const Text(
          'Фабричная упакованная еда. Съеденную порцию укажем отдельно.',
        ),
        const SizedBox(height: 16),
        OutlinedButton.icon(
          onPressed: busy || running ? null : scan,
          icon: const Icon(Icons.qr_code_scanner),
          label: const Text('Сканировать упаковку'),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: code,
          enabled: !busy && !running,
          keyboardType: TextInputType.number,
          maxLength: 14,
          inputFormatters: [FilteringTextInputFormatter.digitsOnly],
          decoration: const InputDecoration(
            labelText: 'Штрихкод',
            hintText: 'Можно ввести цифры вручную',
          ),
          onChanged: (_) {
            setState(() {
              canonical = null;
              product = null;
              job = null;
              detail = null;
              imagePath = null;
              imageId = null;
            });
            unawaited(
              persist().catchError((Object e) {
                if (mounted) setState(() => error = readableError(e));
              }),
            );
          },
        ),
        FilledButton(
          onPressed: busy || running || code.text.trim().isEmpty
              ? null
              : lookup,
          child: const Text('Найти продукт'),
        ),
        if (busy)
          const Padding(
            padding: EdgeInsets.all(16),
            child: Center(child: CircularProgressIndicator()),
          ),
        if (detail != null)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 16),
            child: Text(detail!),
          ),
        if (error != null)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 16),
            child: Text(error!, style: const TextStyle(color: Colors.red)),
          ),
        if (product != null) ...[
          const SizedBox(height: 16),
          Text(product!['name'], style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: 8),
          Text(
            '${number(product!['kcal_per_100g'])} ккал на 100 ${product!['unit']}',
          ),
          Text(
            'Б/Ж/У ${product!['verified'] == true ? '' : '≈ '}${number(product!['protein_per_100g'])}/${number(product!['fat_per_100g'])}/${number(product!['carbs_per_100g'])} г',
          ),
          const SizedBox(height: 8),
          Text(
            product!['verified'] == true
                ? 'Ранее сверено с упаковкой'
                : 'Значения требуют проверки по упаковке',
          ),
          if (product!['attribution'] != null) ...[
            TextButton(
              onPressed: () => openLink(
                product!['source_url'] ?? 'https://world.openfoodfacts.org',
              ),
              child: const Text('Источник: Open Food Facts'),
            ),
            TextButton(
              onPressed: () =>
                  openLink('https://opendatacommons.org/licenses/odbl/1-0/'),
              child: const Text('Данные по лицензии ODbL'),
            ),
          ],
        ],
        if (running) ...[
          const SizedBox(height: 20),
          const Text(
            'Grok читает этикетку. Можно вернуться назад и продолжить позже.',
          ),
          if (pendingLabel != null)
            TextButton(
              onPressed: busy ? null : retry,
              child: const Text('Продолжить отправку этикетки'),
            ),
          if (job != null)
            TextButton(
              onPressed: poll,
              child: const Text('Проверить результат'),
            ),
        ],
        if (job?['status'] == 'failed')
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 16),
            child: Text(job?['error'] ?? 'Не удалось прочитать этикетку'),
          ),
        if (canonical != null && !running) ...[
          const SizedBox(height: 20),
          FilledButton(
            onPressed: busy ? null : add,
            child: Text(
              product == null
                  ? 'Ввести данные и порцию'
                  : 'Проверить и указать порцию',
            ),
          ),
          const SizedBox(height: 8),
          OutlinedButton.icon(
            onPressed: busy ? null : readLabel,
            icon: const Icon(Icons.document_scanner_outlined),
            label: const Text('Прочитать фото этикетки'),
          ),
          if (imagePath != null && job == null)
            TextButton(
              onPressed: busy ? null : retry,
              child: const Text('Продолжить разбор выбранного фото'),
            ),
          const Padding(
            padding: EdgeInsets.only(top: 12),
            child: Text(
              'Фото таблицы КБЖУ можно выбрать из галереи. Неизвестные значения останутся пустыми.',
            ),
          ),
        ],
        const SizedBox(height: 16),
        TextButton(
          onPressed: busy
              ? null
              : () async {
                  await widget.onDraft(null);
                  if (context.mounted) Navigator.of(context).pop();
                },
          child: const Text('Отменить добавление'),
        ),
      ],
    ),
  );
}
