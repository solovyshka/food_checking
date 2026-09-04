#!/usr/bin/env bash
# Push local .env into the closed SSH secrets vault.
# Usage: ./deploy/secrets/push.sh [project] [source]
# Example: ./deploy/secrets/push.sh food_checking
set -euo pipefail

HOST="${SECRETS_HOST:-brynn}"
REMOTE_ROOT="${SECRETS_REMOTE_ROOT:-/opt/secrets}"
PROJECT="${1:-food_checking}"
SOURCE="${2:-.env}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [[ "$SOURCE" != /* ]]; then
  SOURCE="$REPO_ROOT/$SOURCE"
fi

if [[ ! -f "$SOURCE" ]]; then
  echo "Missing local file: $SOURCE" >&2
  exit 1
fi

REMOTE_DIR="$REMOTE_ROOT/$PROJECT"
REMOTE_FILE="$REMOTE_DIR/.env"

echo "Pushing $SOURCE -> $HOST:$REMOTE_FILE"
ssh -o BatchMode=yes -o ConnectTimeout=15 "$HOST" "mkdir -p '$REMOTE_DIR' && chmod 700 '$REMOTE_DIR'"
scp -o BatchMode=yes -o ConnectTimeout=15 "$SOURCE" "$HOST:$REMOTE_FILE"
ssh -o BatchMode=yes -o ConnectTimeout=15 "$HOST" "chmod 600 '$REMOTE_FILE'"
echo "OK: vault updated for $PROJECT"
