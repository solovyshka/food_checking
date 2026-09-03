#!/usr/bin/env bash
# On the server: copy vault .env into an app directory and optionally restart.
# Usage (on brynn): bash apply-local.sh food_checking /opt/food_checking [food-api food-bot]
set -euo pipefail

REMOTE_ROOT="${SECRETS_REMOTE_ROOT:-/opt/secrets}"
PROJECT="${1:-food_checking}"
APP_DIR="${2:-/opt/food_checking}"
shift 2 || true
SERVICES=("$@")

SRC="$REMOTE_ROOT/$PROJECT/.env"
DST="$APP_DIR/.env"

if [[ ! -f "$SRC" ]]; then
  echo "Missing vault file: $SRC" >&2
  exit 1
fi

cp "$SRC" "$DST"
chmod 600 "$DST"
echo "Applied $SRC -> $DST"

if ((${#SERVICES[@]})); then
  systemctl restart "${SERVICES[@]}"
  systemctl --no-pager --lines=5 status "${SERVICES[@]}" || true
fi
