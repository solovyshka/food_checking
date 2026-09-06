#!/bin/bash
set -e
SID='1K2Scnoo9-k1amFRt8ZcSPFIhLznZDm5EvfJbJoclN7A'
SA='/opt/secrets/food_checking/google-sa.json'
for f in /opt/secrets/food_checking/.env /opt/food_checking/.env; do
  [ -f "$f" ] || touch "$f"
  if grep -q '^GOOGLE_SHEETS_SPREADSHEET_ID=' "$f"; then
    sed -i "s|^GOOGLE_SHEETS_SPREADSHEET_ID=.*|GOOGLE_SHEETS_SPREADSHEET_ID=$SID|" "$f"
  else
    echo "GOOGLE_SHEETS_SPREADSHEET_ID=$SID" >> "$f"
  fi
  if grep -q '^GOOGLE_SERVICE_ACCOUNT_FILE=' "$f"; then
    sed -i "s|^GOOGLE_SERVICE_ACCOUNT_FILE=.*|GOOGLE_SERVICE_ACCOUNT_FILE=$SA|" "$f"
  else
    echo "GOOGLE_SERVICE_ACCOUNT_FILE=$SA" >> "$f"
  fi
  grep -q '^GOOGLE_SHEETS_TAB_CURRENT=' "$f" || echo 'GOOGLE_SHEETS_TAB_CURRENT=Текущее' >> "$f"
  grep -q '^GOOGLE_SHEETS_TAB_PROPOSAL=' "$f" || echo 'GOOGLE_SHEETS_TAB_PROPOSAL=Предложение' >> "$f"
done
systemctl restart food-bot
sleep 1
systemctl is-active food-bot
cd /opt/food_checking
PYTHONPATH=/opt/food_checking ./venv/bin/python <<'PY'
from app.config import get_settings
get_settings.cache_clear()
from app.config import get_settings
s = get_settings()
print("has_sheets", s.has_google_sheets)
print("sid_ok", s.google_sheets_spreadsheet_id.startswith("1K2Scnoo9"))
from app.services.google_sheets import _client, export_current_inventory
from app.db.session import SessionLocal
from app.services.inventory import list_inventory
ss = _client()
titles = [ws.title for ws in ss.worksheets()]
print("tabs", titles)
with SessionLocal() as db:
    rows = list_inventory(db, status="confirmed")
export_current_inventory(rows)
print("exported_current", len(rows))
PY
