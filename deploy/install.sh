#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/food_checking"
DB_NAME="food_checking"
DB_USER="food"

DB_PASS="$(openssl rand -hex 16)"

if [[ ! -f "$APP_DIR/.env" ]]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  sed -i "s#postgresql://food:[^@]*@#postgresql://food:${DB_PASS}@#" "$APP_DIR/.env"
  echo "Created $APP_DIR/.env"
  echo "Set TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USER_IDS before starting the bot."
else
  DB_PASS="$(grep '^DATABASE_URL=' "$APP_DIR/.env" | sed -E 's#postgresql://[^:]+:([^@]+)@.*#\1#')"
fi

sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';"

sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"

python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

python3 -m venv "$APP_DIR/whisper-venv"
"$APP_DIR/whisper-venv/bin/pip" install --upgrade pip
"$APP_DIR/whisper-venv/bin/pip" install -r "$APP_DIR/whisper/requirements.txt"

mkdir -p "$APP_DIR/whisper_models"
cd "$APP_DIR"
"$APP_DIR/venv/bin/alembic" upgrade head

install -m 644 "$APP_DIR/deploy/systemd/food-whisper.service" /etc/systemd/system/
install -m 644 "$APP_DIR/deploy/systemd/food-api.service" /etc/systemd/system/
install -m 644 "$APP_DIR/deploy/systemd/food-bot.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable food-whisper food-api food-bot
systemctl restart food-whisper food-api || true
systemctl restart food-bot || true

echo "Install complete."
echo "Edit $APP_DIR/.env then: systemctl restart food-bot food-api food-whisper"
