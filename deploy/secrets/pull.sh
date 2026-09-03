#!/usr/bin/env bash
# Pull project .env from the closed SSH secrets vault.
# Usage: ./deploy/secrets/pull.sh [project] [dest]
# Example: ./deploy/secrets/pull.sh food_checking
#          ./deploy/secrets/pull.sh food_checking /opt/food_checking/.env
set -euo pipefail

HOST="${SECRETS_HOST:-brynn}"
REMOTE_ROOT="${SECRETS_REMOTE_ROOT:-/opt/secrets}"
PROJECT="${1:-food_checking}"
DEST="${2:-.env}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [[ "$DEST" != /* ]]; then
  DEST="$REPO_ROOT/$DEST"
fi

REMOTE_FILE="$REMOTE_ROOT/$PROJECT/.env"

echo "Pulling $HOST:$REMOTE_FILE -> $DEST"
scp -o BatchMode=yes -o ConnectTimeout=15 "$HOST:$REMOTE_FILE" "$DEST"
chmod 600 "$DEST"
echo "OK: $DEST"
