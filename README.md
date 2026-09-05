# Food Checking

Учёт продуктов и калорий голосом/текстом через два Telegram-бота.  
Стек на сервере **brynn**: GigaAM (+ Whisper fallback), Qwen (Ollama), PostgreSQL 16, systemd + venv.

Полная схема: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**. Секреты: **[docs/secrets.md](docs/secrets.md)**.

## Компоненты

| Unit | Роль |
|------|------|
| `food-bot` | Наличие (`app.bot.inventory`, `TELEGRAM_BOT_TOKEN`) |
| `food-eat-bot` | Калории / «съел» (`app.bot.consumption`, `TELEGRAM_CONSUMPTION_BOT_TOKEN`) |
| `food-api` | FastAPI `127.0.0.1:8088` |
| `food-gigaam` | STT primary `:9001` |
| `food-whisper` | STT fallback `:9000` |
| Postgres + Ollama | уже на хосте |

Боты пишут в БД напрямую; API — отдельный HTTP-вход.

## Потоки

**Наличие:** голос → STT → сразу Qwen → таблица → Подтвердить/Отменить → `inventory_entries`.

**Съел:** голос/текст → подтверждение текста → очередь `consumption_transcripts` по периоду (дата + завтрак/обед/ужин/перекус) → кнопка **Разобрать транскрибации** → один прогон Qwen → `consumption_entries` (г/мл, опционально ккал/100г).

## Локальная разработка

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --port 8088
python -m app.bot.inventory      # наличие
python -m app.bot.consumption    # калории
```

GigaAM / Whisper — отдельные venv и процессы (см. `gigaam/`, `whisper/`).

## Деплой (161.104.53.72)

```bash
rsync -avz --exclude venv --exclude whisper-venv --exclude gigaam-venv --exclude .git \
  . root@161.104.53.72:/opt/food_checking/
ssh root@161.104.53.72 'bash /opt/food_checking/deploy/install.sh'
# GigaAM (если ещё нет):
# ssh ... 'bash /opt/food_checking/deploy/install-gigaam-cpu.sh'
```

В `/opt/food_checking/.env` (и vault `/opt/secrets/food_checking/.env`):

- `TELEGRAM_BOT_TOKEN` — наличие  
- `TELEGRAM_CONSUMPTION_BOT_TOKEN` — калории  
- `TELEGRAM_ALLOWED_USER_IDS`  
- `DATABASE_URL`, STT/Ollama ключи — см. `.env.example`

```bash
systemctl restart food-gigaam food-whisper food-api food-bot food-eat-bot
systemctl status food-gigaam food-whisper food-api food-bot food-eat-bot
```

## API

- `GET /health`
- `POST /api/voice/process?kind=inventory|consumption`
- `POST /api/text/process?text=...&kind=consumption|inventory`
- `GET /api/inventory`, `GET /api/consumption` (`?status=confirmed`)
- `POST /api/{inventory|consumption}/{batch_id}/confirm|cancel`

Разбор очереди транскриптов — только из бота калорий.

## OpenAI / VPN (опционально)

Не используется основными ботами. Скрипт split-VPN и клиент остаются для экспериментов (`deploy/vpn/hideme-openai.sh`, `app/services/compare.py`). OVPN только на сервере в `/opt/secrets/food_checking/vpn/`.

## Секреты

Реальные `.env` не в git. Vault: `/opt/secrets/<project>/.env`.

```bash
./deploy/secrets/pull.sh food_checking
./deploy/secrets/push.sh food_checking
ssh brynn 'bash /opt/food_checking/deploy/secrets/apply-local.sh food_checking /opt/food_checking food-api food-bot food-eat-bot'
```

## План (история)

Ранний roadmap: [PLAN.md](PLAN.md). Актуальная архитектура — [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
