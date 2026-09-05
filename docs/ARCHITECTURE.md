# Архитектура Food Checking

Актуально на 2026-09-05. Сервер: **brynn** (`161.104.53.72`), приложение в `/opt/food_checking`.

## Назначение

Два независимых Telegram-бота на одной БД и общем STT/LLM:

| Бот | Процесс | Токен | Задача |
|-----|---------|-------|--------|
| Наличие | `food-bot` → `python -m app.bot.inventory` | `TELEGRAM_BOT_TOKEN` | Что есть дома (голос → сразу Qwen) |
| Калории / «съел» | `food-eat-bot` → `python -m app.bot.consumption` | `TELEGRAM_CONSUMPTION_BOT_TOKEN` | Съеденное (текст в очередь → Qwen по периоду) |

Боты ходят в Postgres и сервисы **напрямую** (не через `food-api`). FastAPI — параллельный HTTP-интерфейс.

## Схема рантайма

```text
Telegram
   │
   ├─ food-bot (inventory) ──────────────┐
   └─ food-eat-bot (consumption) ────────┤
                                         ▼
                              ┌──────────────────┐
                              │  PostgreSQL 16   │
                              │  food_checking   │
                              └──────────────────┘
                                         ▲
food-api :8088 ──────────────────────────┘

STT / LLM (localhost only):
  food-gigaam  :9001   primary STT (GigaAM, keep-alive)
  food-whisper :9000   fallback STT
  ollama       :11434  Qwen 2.5 7B instruct
```

## Systemd-юниты

| Unit | Bind | Роль |
|------|------|------|
| `food-api` | `127.0.0.1:8088` | FastAPI |
| `food-bot` | long-poll | Наличие |
| `food-eat-bot` | long-poll | Потребление |
| `food-gigaam` | `127.0.0.1:9001` | GigaAM STT |
| `food-whisper` | `127.0.0.1:9000` | Whisper STT |
| (host) `postgresql` | `5432` | БД |
| (host) `ollama` | `127.0.0.1:11434` | LLM |

`deploy/install.sh` ставит api/bot/eat-bot/whisper. GigaAM — отдельно: `deploy/install-gigaam-cpu.sh` + `deploy/food-gigaam.service`.

## Потоки данных

### Наличие (deferred)

```text
голос → STT → inventory_transcripts (pending)
  → пользователь подтверждает текст (queued)
  → «Разобрать транскрибации» по дню
  → один вызов Qwen → pending inventory_entries
  → Подтвердить / Отменить (HTML-отчёт)
```

### Съел (deferred)

```text
голос → STT ─┐
текст ───────┴→ consumption_transcripts (pending)
                  → пользователь подтверждает текст (queued)
                  → «Разобрать транскрибации» по периоду
                       (дата + завтрак|обед|ужин|перекус)
                  → один вызов Qwen по всем текстам периода
                  → pending consumption_entries
                  → Подтвердить → entries confirmed + transcripts parsed
                  → Отменить   → entries cancelled, тексты снова queued
```

Период (Москва) по `recorded_at`:

- 05:00–10:59 → завтрак  
- 11:00–15:59 → обед  
- 16:00–21:59 → ужин  
- иначе → перекус  

## Модель данных

### `products`
`name`, `name_normalized` (unique), `unit`. Наличие — справочник `app/catalog`. Съел — свободные имена.

### `inventory_entries` / `consumption_entries`
Общее: quantity, unit, status (`pending|confirmed|cancelled`), source, transcript, batch_id, recorded_at, entry_date.  
Только consumption: `kcal_per_100g`.

### `consumption_transcripts`
Очередь до разбора: text, status (`pending|queued|parsed|cancelled`), meal_type, entry_date, parse_batch_id, stt_backend.

Миграции: `0001` → `0002` → `0003_kcal` → `0004_transcripts`.

## Ключевые модули

```text
app/bot/
  inventory.py      # бот наличия
  consumption.py    # бот калорий
  common.py         # ACL, IPv4 Telegram session
  main.py           # compat → inventory

app/services/
  voice_pipeline.py # ветвление inventory vs consumption
  transcripts.py    # очередь + parse_period + finalize
  transcription.py  # GigaAM / Whisper
  parser.py         # Ollama Qwen
  inventory.py      # batches, списки

app/main.py         # FastAPI
app/db/models.py
deploy/systemd/     # unit-файлы
docs/secrets.md     # vault /opt/secrets
```

## API (HTTP)

| Метод | Путь | Примечание |
|-------|------|------------|
| GET | `/health` | |
| POST | `/api/voice/process?kind=` | consumption → transcript preview |
| POST | `/api/text/process?kind=` | |
| GET | `/api/inventory`, `/api/consumption` | |
| POST | `/api/{kind}/{batch_id}/confirm\|cancel` | consumption → `finalize_parse` |

Подтверждение транскрипта и разбор периода — **только в боте** (пока без HTTP).

## STT

`VOICE_STT_BACKEND=gigaam` (по умолчанию) → GigaAM; при ошибке → Whisper (`whisper-fallback`).  
`VOICE_STT_BACKEND=whisper` — только Whisper.

## Опционально / не в основном пути

Код OpenAI + HideMyName VPN (`compare.py`, `food_diary.py`, `hideme_vpn.py`) остаётся для экспериментов. Боты его не вызывают.

## Таблицы в Telegram

Списки и превью перед записью отдаются **ссылкой** на статический HTML:

- файлы: `/opt/food_checking/var/reports/<token>.html`
- nginx: `http://161.104.53.72/r/<token>.html` (только `/r/`, без листинга)
- генерация: `app/services/reports.py`
- установка nginx: `deploy/install-nginx-reports.sh`

Имя файла — случайный токен (не угадывается). API на `:8088` по-прежнему только localhost.

- Vault: `/opt/secrets/food_checking/.env`  
- App: `/opt/food_checking/.env`  
- Скрипты: `deploy/secrets/{pull,push,apply-local,bypass-vpn}.sh`  
- После правок env: `systemctl restart food-api food-bot food-eat-bot` (+ whisper/gigaam при смене STT)

Подробнее: [secrets.md](secrets.md).
