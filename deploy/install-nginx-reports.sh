#!/usr/bin/env bash
# Install nginx site that serves /opt/food_checking/var/reports at http://HOST/r/
set -euo pipefail

APP_DIR="${1:-/opt/food_checking}"
REPORTS_DIR="$APP_DIR/var/reports"
CONF_SRC="$APP_DIR/deploy/nginx/food-reports.conf"
CONF_DST="/etc/nginx/sites-available/food-reports"

mkdir -p "$REPORTS_DIR"
chmod 755 "$APP_DIR/var" "$REPORTS_DIR"

apt-get update -qq
apt-get install -y -qq nginx

install -m 644 "$CONF_SRC" "$CONF_DST"
rm -f /etc/nginx/sites-enabled/default
ln -sfn "$CONF_DST" /etc/nginx/sites-enabled/food-reports

nginx -t
systemctl enable nginx
systemctl restart nginx

echo "nginx food-reports OK"
echo "Reports dir: $REPORTS_DIR"
echo "Try: curl -sI http://127.0.0.1/r/"
