part of 'main.dart';

class DailyEnergy extends StatelessWidget {
  const DailyEnergy({super.key, required this.diary});
  final Map<String, dynamic>? diary;

  @override
  Widget build(BuildContext context) {
    final delta = double.tryParse('${diary?['energy_delta']}');
    final approximate =
        diary?['energy_delta_estimated'] == true ||
        diary?['energy_delta_incomplete'] == true;
    final difference = delta == null
        ? '—'
        : '${approximate ? '≈ ' : ''}${delta > 0 ? '+' : ''}${number(delta)} ккал';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Divider(color: Color(0xFF66877A)),
        if (diary?['energy_mode'] != 'legacy_total') ...[
          Text(
            'В покое: ${diary?['resting_kcal'] == null ? '—' : '≈ ${number(diary!['resting_kcal'])} ккал'}',
            style: const TextStyle(color: Colors.white, fontSize: 17),
          ),
          const SizedBox(height: 8),
          Text(
            'Тренировки: ${diary?['training_kcal'] == null ? '—' : '${number(diary!['training_kcal'])} ккал'}',
            style: const TextStyle(color: Colors.white, fontSize: 17),
          ),
          if (diary?['resting_kcal'] == null)
            const Text(
              'Заполните профиль в настройках. Для прошлых дней нужны данные веса на тот день.',
              style: TextStyle(color: Color(0xFFCEE0D6), fontSize: 12),
            ),
        ] else
          const Text(
            'Ранее введён общий расход за день',
            style: TextStyle(color: Color(0xFFCEE0D6), fontSize: 12),
          ),
        const SizedBox(height: 8),
        Text(
          'Потрачено: ${diary?['spent_kcal'] == null ? '—' : '${number(diary!['spent_kcal'])} ккал'}',
          style: const TextStyle(color: Colors.white, fontSize: 17),
        ),
        const SizedBox(height: 8),
        Text(
          'Разница: $difference',
          style: const TextStyle(
            color: Colors.white,
            fontSize: 22,
            fontWeight: FontWeight.w600,
          ),
        ),
        const SizedBox(height: 4),
        const Text(
          'Съедено − потрачено',
          style: TextStyle(color: Color(0xFFCEE0D6), fontSize: 12),
        ),
        if (diary?['energy_mode'] == 'rest_only')
          const Text(
            'Учтён только покой: тренировки не указаны.',
            style: TextStyle(color: Color(0xFFCEE0D6), fontSize: 12),
          )
        else if (diary?['energy_mode'] != 'legacy_total')
          const Text(
            'Расчёт: покой + тренировки. Бытовая активность отдельно не учтена.',
            style: TextStyle(color: Color(0xFFCEE0D6), fontSize: 12),
          ),
        if (diary?['energy_delta_incomplete'] == true)
          const Padding(
            padding: EdgeInsets.only(top: 6),
            child: Text(
              'По записанной еде: часть калорий ещё не рассчитана',
              style: TextStyle(color: Color(0xFFCEE0D6), fontSize: 12),
            ),
          ),
      ],
    );
  }
}

Future<bool?> dailyEnergyDialog(
  BuildContext context,
  Api api,
  String date,
  dynamic original,
) async {
  return showDialog<bool>(
    context: context,
    barrierDismissible: false,
    builder: (_) =>
        _DailyEnergyDialog(api: api, date: date, original: original),
  );
}

class _DailyEnergyDialog extends StatefulWidget {
  const _DailyEnergyDialog({
    required this.api,
    required this.date,
    required this.original,
  });
  final Api api;
  final String date;
  final dynamic original;
  @override
  State<_DailyEnergyDialog> createState() => _DailyEnergyDialogState();
}

class _DailyEnergyDialogState extends State<_DailyEnergyDialog> {
  late final TextEditingController input;
  final form = GlobalKey<FormState>();
  bool busy = false;
  String? error;
  @override
  void initState() {
    super.initState();
    input = TextEditingController(
      text: widget.original == null ? '' : '${widget.original}',
    );
  }

  @override
  void dispose() {
    input.dispose();
    super.dispose();
  }

  Future<void> save(String? value) async {
    if (busy) return;
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await widget.api.request(
        'PUT',
        '/days/${widget.date}/activity',
        body: {'training_kcal': value},
      );
      if (mounted) {
        Navigator.pop(context, true);
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          busy = false;
          error = errorText(e);
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) => PopScope(
    canPop: !busy,
    child: AlertDialog(
      title: const Text(
        'Калории от тренировок',
        style: TextStyle(fontSize: 22),
      ),
      content: SingleChildScrollView(
        child: Form(
          key: form,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(widget.date),
              const SizedBox(height: 12),
              const Text(
                'Дополнительные калории от тренировок за день. Без тренировок — 0. Расход в покое добавится автоматически.',
              ),
              const SizedBox(height: 16),
              TextFormField(
                key: const ValueKey('training_kcal'),
                controller: input,
                enabled: !busy,
                keyboardType: const TextInputType.numberWithOptions(
                  decimal: true,
                ),
                style: const TextStyle(fontSize: 16),
                decoration: const InputDecoration(
                  labelText: 'Тренировки, ккал',
                  labelStyle: TextStyle(fontSize: 14),
                ),
                validator: (value) {
                  final text = (value ?? '').trim().replaceAll(',', '.');
                  final number = double.tryParse(text);
                  if (number == null ||
                      !number.isFinite ||
                      number < 0 ||
                      number > 100000 ||
                      !RegExp(r'^\d+(\.\d{1,2})?$').hasMatch(text)) {
                    return 'От 0 до 100000, до 2 знаков';
                  }
                  return null;
                },
              ),
              if (error != null)
                Padding(
                  padding: const EdgeInsets.only(top: 12),
                  child: Text(
                    error!,
                    style: const TextStyle(color: Colors.red),
                  ),
                ),
              if (busy)
                const Padding(
                  padding: EdgeInsets.only(top: 16),
                  child: Center(child: CircularProgressIndicator()),
                ),
              if (widget.original != null)
                TextButton(
                  onPressed: busy ? null : () => save(null),
                  child: const Text('Убрать значение'),
                ),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: busy ? null : () => Navigator.pop(context, false),
          child: const Text('Отмена'),
        ),
        FilledButton(
          onPressed: busy
              ? null
              : () {
                  if (form.currentState!.validate()) {
                    save(input.text.trim().replaceAll(',', '.'));
                  }
                },
          child: const Text('Сохранить'),
        ),
      ],
    ),
  );
}

Future<bool?> energyProfileDialog(BuildContext context, Api api) =>
    showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (_) => _EnergyProfileDialog(api: api),
    );

class _EnergyProfileDialog extends StatefulWidget {
  const _EnergyProfileDialog({required this.api});
  final Api api;
  @override
  State<_EnergyProfileDialog> createState() => _EnergyProfileDialogState();
}

class _EnergyProfileDialogState extends State<_EnergyProfileDialog> {
  final weight = TextEditingController();
  final height = TextEditingController();
  final name = TextEditingController();
  final birth = TextEditingController();
  DateTime? birthday;
  String? sex;
  final form = GlobalKey<FormState>();
  Map<String, dynamic>? profile;
  bool loading = true;
  bool busy = false;
  String? error;
  @override
  void initState() {
    super.initState();
    load();
  }

  @override
  void dispose() {
    weight.dispose();
    height.dispose();
    name.dispose();
    birth.dispose();
    super.dispose();
  }

  Future<void> load() async {
    try {
      final value = await widget.api.request('GET', '/energy/profile');
      if (!mounted) return;
      weight.text = value['weight_kg'] == null ? '' : '${value['weight_kg']}';
      height.text = value['height_cm'] == null ? '' : '${value['height_cm']}';
      name.text = '${value['name'] ?? ''}';
      sex = value['sex'] as String?;
      birthday = DateTime.tryParse('${value['birth_date']}');
      birth.text = birthday == null
          ? ''
          : '${birthday!.day.toString().padLeft(2, '0')}.${birthday!.month.toString().padLeft(2, '0')}.${birthday!.year}';
      setState(() {
        profile = value;
        loading = false;
        error = null;
      });
    } catch (e) {
      if (mounted) {
        setState(() {
          loading = false;
          error = errorText(e);
        });
      }
    }
  }

  String? validate(String? value, double min, double max) {
    final text = (value ?? '').trim().replaceAll(',', '.');
    final n = double.tryParse(text);
    return n == null ||
            !n.isFinite ||
            n < min ||
            n > max ||
            !RegExp(r'^\d+(\.\d{1,2})?$').hasMatch(text)
        ? 'От ${number(min)} до ${number(max)}, до 2 знаков'
        : null;
  }

  Future<void> save() async {
    if (busy || !form.currentState!.validate()) return;
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await widget.api.request(
        'PUT',
        '/energy/profile',
        body: {
          'name': name.text.trim(),
          'birth_date': birthday == null ? null : dayKey(birthday!),
          'sex': sex,
          'weight_kg': weight.text.trim().replaceAll(',', '.'),
          'height_cm': height.text.trim().replaceAll(',', '.'),
        },
      );
      if (mounted) Navigator.pop(context, true);
    } catch (e) {
      if (mounted) {
        setState(() {
          busy = false;
          error = errorText(e);
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) => PopScope(
    canPop: !busy,
    child: AlertDialog(
      title: const Text('Вес и расход в покое', style: TextStyle(fontSize: 22)),
      content: SingleChildScrollView(
        child: Form(
          key: form,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              if (loading)
                const Center(child: CircularProgressIndicator())
              else if (profile == null)
                TextButton(
                  onPressed: load,
                  child: const Text('Повторить загрузку'),
                )
              else ...[
                TextFormField(
                  key: const ValueKey('person_name'),
                  controller: name,
                  enabled: !busy,
                  maxLength: 64,
                  style: const TextStyle(fontSize: 16),
                  decoration: const InputDecoration(labelText: 'Имя'),
                  validator: (value) =>
                      (value ?? '').trim().isEmpty ? 'Укажите имя' : null,
                ),
                const SizedBox(height: 16),
                TextFormField(
                  key: const ValueKey('birth_date'),
                  controller: birth,
                  readOnly: true,
                  enabled: !busy,
                  style: const TextStyle(fontSize: 16),
                  decoration: const InputDecoration(
                    labelText: 'Дата рождения',
                    suffixIcon: Icon(Icons.calendar_month),
                  ),
                  validator: (_) =>
                      birthday == null ? 'Укажите дату рождения' : null,
                  onTap: busy
                      ? null
                      : () async {
                          final now = DateTime.now();
                          final value = await showDatePicker(
                            context: context,
                            initialDate:
                                birthday ??
                                DateTime(now.year - 30, now.month, now.day),
                            firstDate: DateTime(1900),
                            lastDate: now,
                          );
                          if (value != null && mounted) {
                            setState(() {
                              birthday = value;
                              birth.text =
                                  '${value.day.toString().padLeft(2, '0')}.${value.month.toString().padLeft(2, '0')}.${value.year}';
                            });
                          }
                        },
                ),
                const SizedBox(height: 16),
                DropdownButtonFormField<String>(
                  key: const ValueKey('person_sex'),
                  initialValue: sex,
                  isExpanded: true,
                  decoration: const InputDecoration(labelText: 'Пол'),
                  items: const [
                    DropdownMenuItem(value: 'male', child: Text('Мужской')),
                    DropdownMenuItem(value: 'female', child: Text('Женский')),
                  ],
                  onChanged: busy
                      ? null
                      : (value) => setState(() => sex = value),
                  validator: (value) => value == null ? 'Укажите пол' : null,
                ),
                const SizedBox(height: 16),
                if (profile!['age'] != null)
                  Text('Возраст: ${profile!['age']}'),
                const SizedBox(height: 16),
                TextFormField(
                  key: const ValueKey('weight_kg'),
                  controller: weight,
                  enabled: !busy,
                  keyboardType: const TextInputType.numberWithOptions(
                    decimal: true,
                  ),
                  style: const TextStyle(fontSize: 16),
                  decoration: const InputDecoration(labelText: 'Вес, кг'),
                  validator: (value) => validate(value, 20, 400),
                ),
                const SizedBox(height: 16),
                TextFormField(
                  key: const ValueKey('height_cm'),
                  controller: height,
                  enabled: !busy,
                  keyboardType: const TextInputType.numberWithOptions(
                    decimal: true,
                  ),
                  style: const TextStyle(fontSize: 16),
                  decoration: const InputDecoration(labelText: 'Рост, см'),
                  validator: (value) => validate(value, 100, 250),
                ),
                const SizedBox(height: 16),
                if (profile!['resting_kcal'] != null)
                  Text(
                    'Сохранённый расчёт в покое: ≈ ${number(profile!['resting_kcal'])} ккал/сутки',
                  ),
                const SizedBox(height: 8),
                const Text(
                  'Вес сохраняется с сегодняшнего дня. Возраст рассчитывается автоматически. Расход в покое — оценка по формуле Миффлина — Сан Жеора.',
                ),
              ],
              if (error != null)
                Padding(
                  padding: const EdgeInsets.only(top: 12),
                  child: Text(
                    error!,
                    style: const TextStyle(color: Colors.red),
                  ),
                ),
              if (busy) const CircularProgressIndicator(),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: busy ? null : () => Navigator.pop(context, false),
          child: const Text('Закрыть'),
        ),
        FilledButton(
          onPressed: busy || loading || profile == null ? null : save,
          child: const Text('Сохранить'),
        ),
      ],
    ),
  );
}
