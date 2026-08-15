#!/bin/bash
# redeploy.sh (Mac Mini)
#
# Deploys this repo's working tree straight to the Mac Mini running the
# Aria interceptor, (re)installs it as a LaunchDaemon, and tails its log
# so you can watch the next weigh-in come in. No zip step -- run this
# from a checkout of the repo and it'll sync exactly what's on disk here.
#
# SSH host/user come from mise (see ../mise.local.toml.example) rather
# than being hardcoded here, so this script stays safe to commit. One-time
# setup:
#   cp mise.local.toml.example mise.local.toml   # from repo root, then edit it
#   mise trust
#   chmod +x mac/redeploy.sh
#   ./mac/redeploy.sh

set -euo pipefail

# --- config: from mise (mise.local.toml), or export these yourself ---
SSH_USER="${ARIA_MAC_SSH_USER:?Set ARIA_MAC_SSH_USER, e.g. in mise.local.toml (see mise.local.toml.example)}"
SSH_HOST="${ARIA_MAC_SSH_HOST:?Set ARIA_MAC_SSH_HOST, e.g. in mise.local.toml (see mise.local.toml.example)}"
REMOTE_DIR="/usr/local/aria_bridge"
LAUNCHD_LABEL="com.aria.interceptor"
# ---------------------------

LAUNCHD_PLIST_REMOTE="/Library/LaunchDaemons/${LAUNCHD_LABEL}.plist"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STAGING_DIR="aria_bridge_staging"   # relative to the remote user's home

echo "==> Syncing $REPO_ROOT/ -> $SSH_USER@$SSH_HOST:~/$STAGING_DIR/ ..."
rsync -avz --delete \
    --exclude '.git' --exclude 'data' --exclude '__pycache__' --exclude '*.pyc' \
    -e ssh \
    "$REPO_ROOT/" "$SSH_USER@$SSH_HOST:~/$STAGING_DIR/"

echo "==> Deploying on $SSH_HOST and (re)installing the LaunchDaemon ..."
echo "    (you may be prompted for your sudo password on the Mac Mini)"
echo

# -t allocates a pty so sudo can prompt for a password interactively.
ssh -t "$SSH_USER@$SSH_HOST" "
    set -e &&
    sudo launchctl bootout system $LAUNCHD_PLIST_REMOTE 2>/dev/null || true &&
    sudo mkdir -p $REMOTE_DIR &&
    sudo rsync -a --delete --exclude data --exclude raw_uploads --exclude __pycache__ \
        ~/$STAGING_DIR/ $REMOTE_DIR/ &&
    sudo cp $REMOTE_DIR/mac/com.aria.interceptor.plist $LAUNCHD_PLIST_REMOTE &&
    sudo chown root:wheel $LAUNCHD_PLIST_REMOTE &&
    sudo chmod 644 $LAUNCHD_PLIST_REMOTE &&
    sudo launchctl bootstrap system $LAUNCHD_PLIST_REMOTE &&
    echo '--- status ---' &&
    sudo launchctl print system/$LAUNCHD_LABEL | head -20 &&
    echo &&
    echo '--- tailing log, Ctrl+C to stop watching (service keeps running) ---' &&
    sudo tail -f /var/log/aria_interceptor.log
"
