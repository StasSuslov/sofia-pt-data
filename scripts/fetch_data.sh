#!/usr/bin/env bash
# Pull collected GTFS-RT data from the collector VPS into the local dataset.
#
# Uses rsync so re-running is cheap: only new/changed bytes transfer, safe to
# run repeatedly while the collector is still writing today's file. Remote
# files are never deleted — clean them up on the server manually once you've
# verified the local copy.
#
# Usage: scripts/fetch_data.sh

set -euo pipefail

VPS_HOST="root@REDACTED-VPS-IP"
VPS_KEY="$HOME/.ssh/sofia_pt_do"
REMOTE_DIR="/opt/sofia-pt/data/"
LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/data/"

echo "Fetching ${VPS_HOST}:${REMOTE_DIR} → ${LOCAL_DIR}"
rsync -avz --progress -e "ssh -i ${VPS_KEY}" "${VPS_HOST}:${REMOTE_DIR}" "${LOCAL_DIR}"
