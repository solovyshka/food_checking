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

## План

См. [PLAN.md](PLAN.md)
