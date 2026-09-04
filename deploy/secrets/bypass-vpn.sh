#!/usr/bin/env bash
# Send SSH traffic to the closed server via LAN, not VPN.
# Usage: ./deploy/secrets/bypass-vpn.sh
set -euo pipefail

SERVER_IP="${SECRETS_SERVER_IP:-161.104.53.72}"
LAN_IF="${SECRETS_LAN_IF:-en0}"

gateway() {
  route -n get -ifscope "$LAN_IF" default 2>/dev/null | awk '/gateway:/{print $2; exit}'
}

GW="$(gateway)"
if [[ -z "$GW" ]]; then
  echo "Cannot find LAN gateway on $LAN_IF" >&2
  exit 1
fi

echo "LAN $LAN_IF gateway: $GW"
echo "Adding host route: $SERVER_IP -> $GW (bypass VPN)"

if route -n get "$SERVER_IP" 2>/dev/null | grep -q "gateway: $GW"; then
  echo "Route already in place."
else
  sudo route -n delete -host "$SERVER_IP" >/dev/null 2>&1 || true
  sudo route -n add -host "$SERVER_IP" "$GW"
fi

echo "Current path:"
route -n get "$SERVER_IP" | egrep 'route to|destination|gateway|interface'
echo
echo "Test: ssh -o BatchMode=yes -o ConnectTimeout=8 brynn hostname"
ssh -o BatchMode=yes -o ConnectTimeout=8 brynn hostname
