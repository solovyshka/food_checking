#!/bin/bash
set -e
for f in /opt/secrets/food_checking/.env /opt/food_checking/.env; do
  if grep -q '^GOOGLE_SHEETS_TAB_CURRENT=' "$f"; then
    sed -i 's|^GOOGLE_SHEETS_TAB_CURRENT=.*|GOOGLE_SHEETS_TAB_CURRENT=наличие|' "$f"
  else
    echo 'GOOGLE_SHEETS_TAB_CURRENT=наличие' >> "$f"
  fi
done
grep GOOGLE_SHEETS /opt/food_checking/.env
systemctl restart food-bot
sleep 1
cd /opt/food_checking
PYTHONPATH=/opt/food_checking ./venv/bin/python <<'PY'
from app.config import get_settings
get_settings.cache_clear()
from app.services.google_sheets import export_current_inventory, spreadsheet_url
from app.db.session import SessionLocal
from app.services.inventory import list_inventory
s = get_settings()
print("tab_current", s.google_sheets_tab_current)
with SessionLocal() as db:
    rows = list_inventory(db, status="confirmed")
export_current_inventory(rows)
print("exported", len(rows))
print(spreadsheet_url())
PY
