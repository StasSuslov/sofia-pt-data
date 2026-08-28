#!/usr/bin/env bash
# Runs fetch_data.sh on a schedule (see deploy/com.sofia-pt.fetch.plist.template)
# and raises a local macOS notification if the pull fails or if the newest
# day file hasn't grown since the last run — a free, account-free alerting
# layer for the laptop-side backup copy.
#
# This only proves the laptop can still reach and pull from the VPS on
# whatever cadence this job runs, and only while the laptop is on and awake.
# It does NOT prove the collector itself is alive between pulls — for that,
# wire collect.py's --healthcheck-url / HEALTHCHECK_URL into an external
# dead-man's-switch service (e.g. https://healthchecks.io) so the VPS pings
# out on its own schedule regardless of this machine's state.

set -uo pipefail  # not -e: we want to always log/notify, not bail out early

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$REPO_ROOT/logs"
LOG_FILE="$LOG_DIR/fetch.log"
STATE_FILE="$LOG_DIR/.last_sizes"
mkdir -p "$LOG_DIR"

notify() {
    local title="$1" message="$2"
    osascript -e "display notification \"$message\" with title \"$title\"" >/dev/null 2>&1 || true
}

log() {
    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') — $1" >> "$LOG_FILE"
}

log "starting scheduled fetch"

if ! "$REPO_ROOT/scripts/fetch_data.sh" >> "$LOG_FILE" 2>&1; then
    log "fetch_data.sh FAILED"
    notify "Sofia PT collector" "Scheduled data pull failed — check logs/fetch.log"
    exit 1
fi

# Crude freshness check: did the most recently modified day file grow since
# the last time this script ran? A stalled collector still lets rsync
# "succeed" (it just re-copies the same bytes), so this catches what the
# exit code above can't.
LATEST_FILE=$(ls -t "$REPO_ROOT"/data/sofia/*.jsonl 2>/dev/null | head -1)
if [[ -n "$LATEST_FILE" ]]; then
    NEW_SIZE=$(stat -f%z "$LATEST_FILE" 2>/dev/null || stat -c%s "$LATEST_FILE")
    PREV_SIZE=$(grep -F "$LATEST_FILE " "$STATE_FILE" 2>/dev/null | tail -1 | cut -d' ' -f2)
    PREV_SIZE="${PREV_SIZE:-0}"

    if [[ "$NEW_SIZE" -le "$PREV_SIZE" ]]; then
        log "WARNING: $(basename "$LATEST_FILE") did not grow ($PREV_SIZE -> $NEW_SIZE bytes)"
        notify "Sofia PT collector" "$(basename "$LATEST_FILE") hasn't grown since last check — collector may be stuck"
    else
        log "$(basename "$LATEST_FILE") grew $PREV_SIZE -> $NEW_SIZE bytes"
    fi

    { grep -vF "$LATEST_FILE " "$STATE_FILE" 2>/dev/null; echo "$LATEST_FILE $NEW_SIZE"; } > "$STATE_FILE.tmp"
    mv "$STATE_FILE.tmp" "$STATE_FILE"
else
    log "WARNING: no data/sofia/*.jsonl files found after fetch"
fi

log "done"
