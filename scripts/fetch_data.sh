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
# never ends up in version control (see CLAUDE.md's infra section for why).
# Copy .env.local.example to .env.local and fill in your own host.
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

LOCAL_DIR="$REPO_ROOT/data/"

# Host masked in the printed/logged line on purpose — this script's own
# stdout gets redirected into logs/fetch.log by scheduled_fetch.sh, and a log
# file is exactly the kind of thing that ends up pasted into an issue or
# shared while debugging. Defeats the point of keeping the address out of
# git if it leaks back out through the log instead.
echo "Fetching ${VPS_HOST%%@*}@<vps>:${REMOTE_DIR} → ${LOCAL_DIR}"
rsync -avz --progress -e "ssh -i ${VPS_KEY}" "${VPS_HOST}:${REMOTE_DIR}" "${LOCAL_DIR}"
