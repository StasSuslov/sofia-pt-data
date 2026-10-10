#!/bin/bash
# Upload one file into a Zenodo draft through the InvenioRDM files API
# (init -> PUT content -> commit), then print what the server recorded.
# The web form stalled three times on the 511 MB static archive of v2 and
# never said why; this went through first time and fails loudly if it stalls.
# Usage: scripts/zenodo_upload.sh <record_id> <file>. Token: ZENODO_TOKEN in .env.local.
set -euo pipefail
id=$1; f=$2; key=$(basename "$f")
token=$(sed -n 's/^ZENODO_TOKEN=//p' "$(dirname "$0")/../.env.local")
[ -n "$token" ] || { echo "ZENODO_TOKEN missing in .env.local" >&2; exit 2; }
api=https://zenodo.org/api/records/$id/draft/files
hdr() { printf 'Authorization: Bearer %s\nAccept: application/vnd.inveniordm.v1+json\n' "$token"; }

echo "local md5 $(md5 -q "$f"), $(stat -f %z "$f") B"
curl -sS --fail-with-body -H @<(hdr) -H 'Content-Type: application/json' \
    -X POST -d "[{\"key\":\"$key\"}]" "$api" -o /dev/null
echo "init ok $(date -u +%T)"
# A stall aborts after 120 s under 10 KB/s instead of hanging like the browser did.
curl -S --fail-with-body -H @<(hdr) -H 'Content-Type: application/octet-stream' \
    --upload-file "$f" "$api/$key/content" -o /dev/null --progress-bar \
    --speed-limit 10240 --speed-time 120 \
    -w '\nput http %{http_code}, %{size_upload} B in %{time_total} s\n'
curl -sS --fail-with-body -H @<(hdr) -X POST "$api/$key/commit" -o /dev/null
echo "commit ok $(date -u +%T)"
curl -sS -H @<(hdr) "$api/$key" | python3 -c \
    'import json,sys; e=json.load(sys.stdin); print(e["key"], e["status"], e.get("size"), e.get("checksum"))'
