#!/usr/bin/env bash
# Send SSH traffic to the closed server via LAN, not VPN.
# Usage: ./deploy/secrets/bypass-vpn.sh
set -euo pipefail

SERVER_IP="${SECRETS_SERVER_IP:-161.104.53.72}"
FIREBAT_WAN_IP="${FIREBAT_WAN_IP:-109.63.220.135}"
LAN_IF="${SECRETS_LAN_IF:-en0}"

gateway() {
  route -n get -ifscope "$LAN_IF" default 2>/dev/null | awk '/gateway:/{print $2; exit}'
}

add_bypass() {
  local ip="$1"
  echo "Adding host route: $ip -> $GW (bypass VPN)"
  if route -n get -host "$ip" 2>/dev/null | grep -q "gateway: $GW"; then
    echo "Route already in place for $ip."
    return 0
  fi
  sudo route -n delete -host "$ip" >/dev/null 2>&1 || true
  sudo route -n add -host "$ip" "$GW"
}

GW="$(gateway)"
if [[ -z "$GW" ]]; then
  echo "Cannot find LAN gateway on $LAN_IF" >&2
  exit 1
fi

echo "LAN $LAN_IF gateway: $GW"
add_bypass "$SERVER_IP"
add_bypass "$FIREBAT_WAN_IP"

echo "Current path ($SERVER_IP):"
route -n get -host "$SERVER_IP" | egrep 'route to|destination|gateway|interface'
echo "Current path ($FIREBAT_WAN_IP):"
route -n get -host "$FIREBAT_WAN_IP" | egrep 'route to|destination|gateway|interface'
echo
echo "Test: ssh -o BatchMode=yes -o ConnectTimeout=8 brynn hostname"
ssh -o BatchMode=yes -o ConnectTimeout=8 brynn hostname
