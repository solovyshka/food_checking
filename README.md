# Food Checking

Учёт продуктов голосом через Telegram-бот. Локальный Whisper + Qwen (Ollama) + PostgreSQL 16.

## Архитектура

- `food-bot` — Telegram-бот (aiogram)
- `food-api` — FastAPI (`/api/voice/process`, `/api/inventory`)
- `food-whisper` — faster-whisper HTTP `/transcribe`
- PostgreSQL 16 — база `food_checking`
- Ollama — уже на сервере, модель `qwen2.5:7b-instruct-q4_K_M`

Обработка последовательная: сначала STT, потом парсинг. Whisper выгружает модель после каждого запроса; Ollama `keep_alive=0`.

## Локальная разработка

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --port 8088
python -m app.bot.main
```

Whisper отдельно:

```bash
python3 -m venv whisper-venv
source whisper-venv/bin/activate
pip install -r whisper/requirements.txt
python whisper/server.py
```

## Деплой на сервер (161.104.53.72)

```bash
rsync -avz --exclude venv --exclude whisper-venv --exclude .git . root@161.104.53.72:/opt/food_checking/
ssh root@161.104.53.72 'bash /opt/food_checking/deploy/install.sh'
```

Затем в `/opt/food_checking/.env`:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_ALLOWED_USER_IDS`

```bash
systemctl restart food-whisper food-api food-bot
systemctl status food-whisper food-api food-bot
```

## API

- `POST /api/voice/process` — multipart `file`, опционально `telegram_message_id`
- `GET /api/inventory?status=confirmed`
- `POST /api/inventory/{batch_id}/confirm`
- `POST /api/inventory/{batch_id}/cancel`

## Бот: режимы

Разные клавиатуры:

- **Наличие** — только голос → `inventory_entries`
- **Съел** — голос или текст → `consumption_entries`
- **Сравн. наличие** — local vs OpenAI (схема наличия), без БД
- **Сравн. съел** — local vs OpenAI дневник (`meal_type`…), без БД

Модели OpenAI по умолчанию: `gpt-4o-mini` (дневник), `gpt-transcribe` (STT).
В сравнении голоса также: local Whisper + **GigaAM** (`v3_e2e_ctc`, сервис `:9001`).

После записи (не сравнение) — Подтвердить / Отменить.

API:

- `POST /api/voice/process?kind=inventory|consumption`
- `POST /api/text/process?text=...&kind=consumption`
- `GET /api/inventory`, `GET /api/consumption`
- `POST /api/{inventory|consumption}/{batch_id}/confirm|cancel`

## OpenAI напрямую (через HideMyName split-VPN)

С сервера `api.openai.com` без VPN даёт 403 (регион). В режиме **Сравнение** бот сам:

1. поднимает HideMyName OpenVPN **только** с маршрутами на `api.openai.com`
2. дергает OpenAI
3. гасит туннель

`.ovpn` **только на сервере**: `/opt/secrets/food_checking/vpn/` (в git не кладём).  
Скрипт в репо: `deploy/vpn/hideme-openai.sh`.

```bash
HIDEME_VPN_ENABLED=1
HIDEME_VPN_SCRIPT=/opt/food_checking/deploy/vpn/hideme-openai.sh
HIDEME_OVPN_CONF=/opt/secrets/food_checking/vpn/netherlands-split.ovpn
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1
```

## Секреты

Реальные `.env` **не в git**. Хранятся на закрытом сервере в `/opt/secrets/<project>/.env`.

```bash
./deploy/secrets/bootstrap-server.sh   # один раз создать vault
./deploy/secrets/bypass-vpn.sh         # SSH мимо VPN (HideMyName)
./deploy/secrets/pull.sh food_checking # новое устройство
./deploy/secrets/push.sh food_checking # сохранить изменения в vault
```

Подробнее: [docs/secrets.md](docs/secrets.md)

## План

См. [PLAN.md](PLAN.md)
