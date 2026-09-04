#!/usr/bin/env bash
# List projects in the remote secrets vault.
set -euo pipefail

HOST="${SECRETS_HOST:-brynn}"
REMOTE_ROOT="${SECRETS_REMOTE_ROOT:-/opt/secrets}"

ssh -o BatchMode=yes -o ConnectTimeout=15 "$HOST" \
  "ls -la '$REMOTE_ROOT'; echo; for d in '$REMOTE_ROOT'/*/; do
     p=\$(basename \"\$d\")
     if [[ -f \"\$d/.env\" ]]; then
       n=\$(grep -cE '^[A-Z0-9_]+=' \"\$d/.env\" || true)
       echo \"\$p: \$n keys\"
     else
       echo \"\$p: (no .env)\"
     fi
   done"
