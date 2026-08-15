#!/bin/bash
# redeploy.sh (Ubuntu box)
#
# Deploys this repo's working tree straight to the Ubuntu box, restarts
# the aria-interceptor systemd service, and tails its logs so you can
# watch the next weigh-in come in. No zip step -- run this from a
# checkout of the repo and it'll sync exactly what's on disk here.
#
# SSH host/user come from mise (see ../mise.local.toml.example) rather
# than being hardcoded here, so this script stays safe to commit. One-time
# setup:
#   cp mise.local.toml.example mise.local.toml   # from repo root, then edit it
#   mise trust
#   chmod +x ubuntu/redeploy.sh
#   ./ubuntu/redeploy.sh

set -euo pipefail

# --- config: from mise (mise.local.toml), or export these yourself ---
SSH_USER="${ARIA_UBUNTU_SSH_USER:?Set ARIA_UBUNTU_SSH_USER, e.g. in mise.local.toml (see mise.local.toml.example)}"
SSH_HOST="${ARIA_UBUNTU_SSH_HOST:?Set ARIA_UBUNTU_SSH_HOST, e.g. in mise.local.toml (see mise.local.toml.example)}"
REMOTE_DIR="/opt/aria_bridge"
SERVICE_NAME="aria-interceptor"
# ---------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STAGING_DIR="aria_bridge_staging"   # relative to the remote user's home

echo "==> Syncing $REPO_ROOT/ -> $SSH_USER@$SSH_HOST:~/$STAGING_DIR/ ..."
rsync -avz --delete \
    --exclude '.git' --exclude 'data' --exclude '__pycache__' --exclude '*.pyc' \
    -e ssh \
    "$REPO_ROOT/" "$SSH_USER@$SSH_HOST:~/$STAGING_DIR/"

echo "==> Deploying on $SSH_HOST, restarting $SERVICE_NAME, and tailing logs ..."
echo "    (you may be prompted for your sudo password on the remote box)"
echo

# -t allocates a pty so sudo can prompt for a password interactively.
# Everything runs in one ssh session so a single sudo prompt covers the
# whole thing (systemd timestamp caches it for the rest of the session).
ssh -t "$SSH_USER@$SSH_HOST" "
    set -e &&
    sudo systemctl stop $SERVICE_NAME || true &&
    sudo mkdir -p $REMOTE_DIR &&
    sudo rsync -a --delete --exclude data --exclude raw_uploads --exclude __pycache__ \
        ~/$STAGING_DIR/ $REMOTE_DIR/ &&
    sudo cp $REMOTE_DIR/ubuntu/aria-interceptor.service /etc/systemd/system/aria-interceptor.service &&
    sudo systemctl daemon-reload &&
    sudo systemctl enable $SERVICE_NAME &&
    sudo systemctl start $SERVICE_NAME &&
    echo '--- systemctl status ---' &&
    sudo systemctl status $SERVICE_NAME --no-pager &&
    echo &&
    echo '--- tailing logs, Ctrl+C to stop watching (service keeps running) ---' &&
    sudo journalctl -u $SERVICE_NAME -f
"
