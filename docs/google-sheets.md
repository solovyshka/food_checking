# Google Sheets для наличия

Один spreadsheet, две вкладки:

| Вкладка | Назначение |
|---------|------------|
| `Текущее` | Выгрузка последнего confirmed наличия из БД |
| `Предложение` | Разбор Qwen + транскрипт; правите вручную → «Добавить в БД» |

## Настройка с нуля

1. [Google Cloud Console](https://console.cloud.google.com/) → создать проект (или взять существующий).
2. APIs & Services → Enable **Google Sheets API**.
3. IAM → Service Accounts → Create → ключ JSON скачать.
4. Создать Google Spreadsheet, переименовать листы в **Текущее** и **Предложение** (или оставить имена из `.env`).
5. Share таблицы на email service account (`...@....iam.gserviceaccount.com`) с правом **Editor**.
6. На сервере:

```bash
# JSON ключ
install -m 600 /path/to/key.json /opt/secrets/food_checking/google-sa.json

# В /opt/secrets/food_checking/.env и /opt/food_checking/.env:
GOOGLE_SHEETS_SPREADSHEET_ID=<id из URL: docs.google.com/spreadsheets/d/THIS_ID/edit>
GOOGLE_SERVICE_ACCOUNT_FILE=/opt/secrets/food_checking/google-sa.json

bash /opt/food_checking/deploy/secrets/apply-local.sh food_checking /opt/food_checking food-bot
systemctl restart food-bot
```

ID таблицы — фрагмент URL между `/d/` и `/edit`.

## Поток в боте

1. Голос → подтверждение текста → очередь.
2. **Разобрать транскрибации** → Qwen → HTML-превью + вкладка **Предложение**; ссылка на предложение в ТГ.
3. **Список наличия** → синхрон вкладки наличия + ссылка на неё.
4. **Добавить предложение** → merge: новые продукты + перезапись пересечений → новый снимок в БД.
5. **Обновить полностью** → наличие = только строки из «Предложение».
