#!/bin/bash
# poll_aria.sh
#
# Runs on your Mac. Polls the Aria interceptor server (on your Ubuntu box)
# for the latest weight reading, and if it's newer than the last one we
# logged, hands it to a Shortcut that writes it into the macOS Health app
# via the "Log Health Sample" action. macOS Health syncs to your iPhone's
# Health app over iCloud (enable that in Health > your profile icon >
# on both devices) -- that's the "cloud" leg that gets it to Apple Health
# without needing Fitbit's servers at all.
#
# Edit SERVER below, then either run this by hand to test, or install it
# as a launchd agent (see com.aria.bridge.poller.plist).

set -euo pipefail

# --- EDIT ME ---
SERVER="http://192.168.1.50:8080"     # the Ubuntu box's LAN IP + port
SHORTCUT_NAME="Aria Weight to Health"  # must match the Shortcut's exact name
# ----------------

# /latest/me only -- the interceptor buckets readings by weight into
# family bands (see FAMILY_BANDS in aria_interceptor.py) since guest mode
# on the scale doesn't distinguish between people. This makes sure only
# your own weigh-ins ever reach your Apple Health, not your kids' or your
# wife's.
STATE_FILE="$HOME/.aria_bridge_last_timestamp"

LATEST_JSON=$(curl -fsS --max-time 5 "$SERVER/latest/me") || { echo "server unreachable, skipping"; exit 0; }

TS=$(echo "$LATEST_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["timestamp"])' 2>/dev/null) || {
    echo "no reading yet, skipping"; exit 0;
}

LAST_TS=0
[ -f "$STATE_FILE" ] && LAST_TS=$(cat "$STATE_FILE")

if [ "$TS" -gt "$LAST_TS" ]; then
    TMP=$(mktemp /tmp/aria_reading.XXXXXX.json)
    echo "$LATEST_JSON" > "$TMP"
    echo "New reading (ts=$TS) -> running Shortcut '$SHORTCUT_NAME'"
    shortcuts run "$SHORTCUT_NAME" --input-path "$TMP"
    echo "$TS" > "$STATE_FILE"
    rm -f "$TMP"
else
    echo "No new reading (latest ts=$TS, last logged ts=$LAST_TS)"
fi
