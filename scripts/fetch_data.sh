#!/usr/bin/env bash
# Pull collected GTFS-RT data from the collector VPS into the local dataset.
#
# Uses rsync so re-running is cheap: only new/changed bytes transfer, safe to
# run repeatedly while the collector is still writing today's file. Remote
# files are never deleted — clean them up on the server manually once you've
# verified the local copy.
#
# Reads VPS_HOST (and optionally VPS_KEY, REMOTE_DIR) from .env.local at the
# repo root — that file is gitignored on purpose, so the server's address
# never ends up in version control. Copy .env.local.example to .env.local
# and fill in your own host.
#
# Usage: scripts/fetch_data.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env.local"

if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$ENV_FILE"
fi

: "${VPS_HOST:?Set VPS_HOST in $ENV_FILE (see .env.local.example), e.g. VPS_HOST=root@203.0.113.5}"
: "${VPS_KEY:=$HOME/.ssh/sofia_pt_do}"
: "${REMOTE_DIR:=/opt/sofia-pt/data/}"
# Absolute path, not a bare "python3" looked up on $PATH: system
# /usr/bin/python3 on macOS is 3.9 and throws TypeError importing
# generate_manifest.py's `X | None` annotations (PEP 604 needs 3.10+), and
# launchd runs scheduled jobs with a bare PATH (/usr/bin:/bin:/usr/sbin:/sbin)
# that finds exactly that broken interpreter even though an interactive shell
# finds a working one first. Override in .env.local on a machine where 3.10+
# lives somewhere else.
: "${PYTHON3:=/usr/local/bin/python3}"

LOCAL_DIR="$REPO_ROOT/data/"
# Exit code fetch_data.sh uses when rsync succeeded but manifest generation
# failed afterwards — deliberately distinct from rsync's own exit codes
# (0-255, with well-known meanings like 23/30) so scheduled_fetch.sh can tell
# "the pull failed" apart from "the pull was fine, manifests didn't update".
MANIFEST_FAILED_EXIT_CODE=42

# Host masked in the printed/logged line on purpose — this script's own
# stdout gets redirected into logs/fetch.log by scheduled_fetch.sh, and a log
# file is exactly the kind of thing that ends up pasted into an issue or
# shared while debugging. Defeats the point of keeping the address out of
# git if it leaks back out through the log instead.
echo "Fetching ${VPS_HOST%%@*}@<vps>:${REMOTE_DIR} → ${LOCAL_DIR}"
rsync -avz --progress -e "ssh -i ${VPS_KEY}" "${VPS_HOST}:${REMOTE_DIR}" "${LOCAL_DIR}"

# Data is safely on disk from here on — a manifest-generation failure past
# this point is a different problem than a failed pull and must be reported
# as one (see MANIFEST_FAILED_EXIT_CODE above and scripts/scheduled_fetch.sh,
# which branches on this exit code). Manifests are cheap to skip when
# current (see generate_manifest.py's --force / manifest_is_current), so
# this runs on every fetch, not just on a schedule of its own.
# No city name spelled out here (CLAUDE.md D2): manifest every directory
# under data/ that actually holds <date>.jsonl day files, so adding a second
# city needs no edit to this script. Sibling directories like
# data/<city>/static/ hold no day files and are skipped. No bash arrays —
# macOS still ships bash 3.2, where ${#arr[@]} on an empty array trips set -u.
manifested=0
for city_dir in "$LOCAL_DIR"*/; do
    ls "${city_dir}"????-??-??.jsonl >/dev/null 2>&1 || continue

    if ! "$PYTHON3" "$REPO_ROOT/scripts/generate_manifest.py" "$city_dir"; then
        echo "generate_manifest.py failed for ${city_dir} — data pulled fine, manifests are stale" >&2
        exit "$MANIFEST_FAILED_EXIT_CODE"
    fi
    manifested=$((manifested + 1))
done

# Finding nothing to checksum after a successful pull is itself a failure:
# staying quiet here is the exact habit that let manifests go three days
# without being regenerated in the first place.
if [[ "$manifested" -eq 0 ]]; then
    echo "No <date>.jsonl day files found under ${LOCAL_DIR} — nothing to checksum" >&2
    exit "$MANIFEST_FAILED_EXIT_CODE"
fi
