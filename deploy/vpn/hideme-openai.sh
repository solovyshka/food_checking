#!/usr/bin/env bash
# HideMyName split-tunnel for OpenAI only (no full redirect-gateway).
# Configs (.ovpn) live ONLY on the server under /opt/secrets — never in git.
#
# Usage:
#   hideme-openai.sh up
#   hideme-openai.sh down
#   hideme-openai.sh status
set -euo pipefail

CONF="${HIDEME_OVPN_CONF:-/opt/secrets/food_checking/vpn/netherlands-split.ovpn}"
LOG="${HIDEME_OVPN_LOG:-/tmp/hideme-openai-openvpn.log}"
PIDFILE="${HIDEME_OVPN_PID:-/tmp/hideme-openai-openvpn.pid}"
ROUTES_FILE="${HIDEME_OVPN_ROUTES:-/tmp/hideme-openai-routes.txt}"
LOCKFILE="${HIDEME_OVPN_LOCK:-/tmp/hideme-openai.lock}"
HOSTS=(api.openai.com)
MAX_WAIT="${HIDEME_OVPN_WAIT:-45}"

need_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    echo "hideme-openai: need root (ip route / openvpn)" >&2
    exit 1
  fi
}

is_up() {
  [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

clear_routes() {
  if [[ -f "$ROUTES_FILE" ]]; then
    while read -r ip; do
      [[ -n "${ip:-}" ]] && ip route del "$ip" 2>/dev/null || true
    done < "$ROUTES_FILE"
    rm -f "$ROUTES_FILE"
  fi
}

do_down() {
  set +e
  if [[ -f "$PIDFILE" ]]; then
    kill "$(cat "$PIDFILE")" 2>/dev/null || true
    sleep 1
    kill -9 "$(cat "$PIDFILE")" 2>/dev/null || true
    rm -f "$PIDFILE"
  fi
  pkill -f "openvpn --config .*netherlands-split" 2>/dev/null || true
  clear_routes
  set -e
  echo "hideme-openai: down"
}

resolve_ips() {
  local host="$1"
  local ips
  ips=$(getent ahostsv4 "$host" 2>/dev/null | awk '{print $1}' | sort -u || true)
  if [[ -z "$ips" ]]; then
    ips=$(dig +short A "$host" 2>/dev/null | grep -E '^[0-9.]+$' | sort -u || true)
  fi
  echo "$ips"
}

do_up() {
  need_root
  if [[ ! -f "$CONF" ]]; then
    echo "hideme-openai: missing config $CONF" >&2
    exit 1
  fi
  if is_up; then
    echo "hideme-openai: already up"
    return 0
  fi

  : > "$LOG"
  : > "$ROUTES_FILE"
  openvpn --config "$CONF" --daemon --writepid "$PIDFILE" --log "$LOG" --verb 3

  local tun=""
  local i
  for i in $(seq 1 "$MAX_WAIT"); do
    tun=$(ip -o link show type tun 2>/dev/null | awk -F': ' '{print $2}' | head -1 || true)
    if [[ -n "$tun" ]] && ip -4 addr show dev "$tun" | grep -q 'inet '; then
      break
    fi
    sleep 1
  done
  if [[ -z "$tun" ]] || ! ip -4 addr show dev "$tun" | grep -q 'inet '; then
    echo "hideme-openai: tun not ready" >&2
    tail -40 "$LOG" >&2 || true
    do_down
    exit 1
  fi

  local tun_ip gw
  tun_ip=$(ip -4 addr show dev "$tun" | awk '/inet /{print $2}' | head -1)
  gw=$(ip -4 route show dev "$tun" | awk '/via/{print $3; exit}')
  if [[ -z "$gw" ]]; then
    gw=$(echo "$tun_ip" | awk -F'[./]' '{print $1"."$2"."$3".1"}')
  fi

  local host ip
  for host in "${HOSTS[@]}"; do
    while read -r ip; do
      [[ -z "$ip" ]] && continue
      ip route replace "$ip" via "$gw" dev "$tun"
      echo "$ip" >> "$ROUTES_FILE"
    done < <(resolve_ips "$host")
  done

  if [[ ! -s "$ROUTES_FILE" ]]; then
    echo "hideme-openai: no API IPs resolved" >&2
    do_down
    exit 1
  fi
  echo "hideme-openai: up tun=$tun gw=$gw routes=$(tr '\n' ' ' < "$ROUTES_FILE")"
}

do_status() {
  if is_up; then
    echo "up pid=$(cat "$PIDFILE")"
    [[ -f "$ROUTES_FILE" ]] && echo "routes: $(tr '\n' ' ' < "$ROUTES_FILE")"
    exit 0
  fi
  echo "down"
  exit 1
}

cmd="${1:-}"
case "$cmd" in
  up)
    need_root
    # Hold lock only while starting; release before openvpn --daemon inherits FDs.
    exec 9>"$LOCKFILE"
    flock -w 120 9
    do_up
    flock -u 9
    exec 9>&-
    ;;
  down)
    need_root
    exec 9>"$LOCKFILE"
    flock -w 120 9
    do_down
    flock -u 9
    exec 9>&-
    ;;
  status)
    do_status
    ;;
  *)
    echo "Usage: $0 {up|down|status}" >&2
    exit 2
    ;;
esac
