#!/usr/bin/env bash
# Create /opt/secrets vault on the closed SSH server.
# Usage: bash deploy/secrets/bootstrap-server.sh
set -euo pipefail

HOST="${SECRETS_HOST:-brynn}"
REMOTE_ROOT="${SECRETS_REMOTE_ROOT:-/opt/secrets}"

ssh -o BatchMode=yes -o ConnectTimeout=15 "$HOST" "REMOTE_ROOT='$REMOTE_ROOT' bash -s" <<'EOF'
set -euo pipefail
mkdir -p "$REMOTE_ROOT"/food_checking \
         "$REMOTE_ROOT"/trading_base_machine \
         "$REMOTE_ROOT"/dailybot
chmod 700 "$REMOTE_ROOT"
chmod 700 "$REMOTE_ROOT"/*

if [[ -f /opt/food_checking/.env && ! -f "$REMOTE_ROOT/food_checking/.env" ]]; then
  cp /opt/food_checking/.env "$REMOTE_ROOT/food_checking/.env"
  echo "Seeded food_checking from /opt/food_checking/.env"
fi

for p in trading_base_machine dailybot; do
  if [[ ! -f "$REMOTE_ROOT/$p/.env" ]]; then
    printf '# Fill secrets for %s\n' "$p" > "$REMOTE_ROOT/$p/.env"
  fi
done

chmod 600 "$REMOTE_ROOT"/*/.env 2>/dev/null || true

cat > "$REMOTE_ROOT/README" <<'NOTE'
Private secrets vault. Not in git.
Layout:
  /opt/secrets/<project>/.env

From a laptop:
  ./deploy/secrets/pull.sh food_checking
  ./deploy/secrets/push.sh food_checking

On this server:
  bash /opt/food_checking/deploy/secrets/apply-local.sh food_checking /opt/food_checking food-api food-bot
NOTE
chmod 644 "$REMOTE_ROOT/README"

echo "Vault ready at $REMOTE_ROOT"
ls -la "$REMOTE_ROOT"
ls -la "$REMOTE_ROOT"/*/
EOF
