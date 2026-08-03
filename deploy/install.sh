#!/usr/bin/env bash
set -euo pipefail

NORA_USER="nora"
NORA_HOME="/home/nora"
REPO_DIR="${NORA_HOME}/workspace/nora"
DATA_DIR="${REPO_DIR}/data"
CONFIG_DIR="/etc/nora"

if ! id -u "$NORA_USER" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "$NORA_HOME" \
        --shell /usr/sbin/nologin --user-group "$NORA_USER"
fi

mkdir -p "$DATA_DIR" "$CONFIG_DIR"
chown -R "$NORA_USER:$NORA_USER" "$REPO_DIR" "$DATA_DIR"
chmod 0750 "$DATA_DIR"

install -m 0644 deploy/nora.service /etc/systemd/system/nora.service

if [ ! -f "$CONFIG_DIR/nora.env" ]; then
    echo "OMNIROUTE_API_KEY=" > "$CONFIG_DIR/nora.env"
    chmod 0600 "$CONFIG_DIR/nora.env"
    chown "$NORA_USER:$NORA_USER" "$CONFIG_DIR/nora.env"
    echo "Edit $CONFIG_DIR/nora.env and set OMNIROUTE_API_KEY before starting the service."
fi

systemctl daemon-reload
systemctl enable nora.service

echo "Installed. Start with: systemctl start nora"
