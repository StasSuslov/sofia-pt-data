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
    # Pass strings as argv (not interpolated into the AppleScript source) so
    # a filename containing a quote can't break out of the script literal.
    osascript -e 'on run argv
        display notification (item 2 of argv) with title (item 1 of argv)
    end run' "$1" "$2" >/dev/null 2>&1 || true
}

log() {
    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') — $1" >> "$LOG_FILE"
}

log "starting scheduled fetch"

# Keep in sync with MANIFEST_FAILED_EXIT_CODE in fetch_data.sh: that's the
# code it returns when rsync succeeded but generate_manifest.py afterwards
# didn't. Data landed fine in that case — reporting it as a failed pull
# would be wrong, so it gets its own log line and notification instead of
# falling into the generic "fetch_data.sh FAILED" branch below.
MANIFEST_FAILED_EXIT_CODE=42
manifest_generation_failed=0

"$REPO_ROOT/scripts/fetch_data.sh" >> "$LOG_FILE" 2>&1
fetch_exit=$?

if [[ "$fetch_exit" -eq "$MANIFEST_FAILED_EXIT_CODE" ]]; then
    log "fetch_data.sh: data pulled OK, generate_manifest.py FAILED"
    notify "Sofia PT collector" "Data pulled OK but manifest generation failed — check logs/fetch.log"
    manifest_generation_failed=1
elif [[ "$fetch_exit" -ne 0 ]]; then
    log "fetch_data.sh FAILED (exit $fetch_exit)"
    notify "Sofia PT collector" "Scheduled data pull failed — check logs/fetch.log"
    exit 1
fi

# Crude freshness check: did the most recently modified day file grow since
# the last time this script ran? A stalled collector still lets rsync
# "succeed" (it just re-copies the same bytes), so this catches what the
# exit code above can't.
#
# Glob is deliberately "????-??-??.jsonl", not a bare "*.jsonl" — the latter
# also matches the "<date>.polls.jsonl" heartbeat file next to it, and the
# heartbeat keeps growing even while the feed is down (collect.py logs a
# heartbeat line on every poll, success or failure — see collect.py's
# heartbeat_path_for). A bare glob could pick the still-growing heartbeat
# file over a frozen data file and report "grew, all fine" during exactly
# the outage this check exists to catch.
LATEST_FILE=$(ls -t "$REPO_ROOT"/data/sofia/????-??-??.jsonl 2>/dev/null | head -1)
if [[ -n "$LATEST_FILE" ]]; then
    NEW_SIZE=$(stat -f%z "$LATEST_FILE" 2>/dev/null || stat --printf=%s "$LATEST_FILE")
    PREV_SIZE=$(awk -F'\t' -v f="$LATEST_FILE" '$1 == f { size = $2 } END { print size + 0 }' "$STATE_FILE" 2>/dev/null)
    PREV_SIZE="${PREV_SIZE:-0}"

    if [[ "$NEW_SIZE" -le "$PREV_SIZE" ]]; then
        log "WARNING: $(basename "$LATEST_FILE") did not grow ($PREV_SIZE -> $NEW_SIZE bytes)"
        notify "Sofia PT collector" "$(basename "$LATEST_FILE") hasn't grown since last check — collector may be stuck"
    else
        log "$(basename "$LATEST_FILE") grew $PREV_SIZE -> $NEW_SIZE bytes"
    fi

    # Tab-delimited (not space) so a repo path containing a space can't
    # corrupt the size lookup above.
    { awk -F'\t' -v f="$LATEST_FILE" '$1 != f' "$STATE_FILE" 2>/dev/null; printf '%s\t%s\n' "$LATEST_FILE" "$NEW_SIZE"; } > "$STATE_FILE.tmp"
    mv "$STATE_FILE.tmp" "$STATE_FILE"
else
    log "WARNING: no data/sofia/????-??-??.jsonl files found after fetch"
fi

log "done"

# Data pulled fine, but exit non-zero (and distinct from a pull failure) so
# this doesn't silently read as full success in launchd's own job status —
# the log line and notification above already said what actually failed.
if [[ "$manifest_generation_failed" -eq 1 ]]; then
    exit "$MANIFEST_FAILED_EXIT_CODE"
fi
