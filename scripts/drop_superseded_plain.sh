#!/usr/bin/env bash
# Usage: drop_superseded_plain.sh <data-dir>
#
# deploy/sofia-compress.service replaces <day>.jsonl with <day>.jsonl.gz in
# place on the VPS, but rsync deletes nothing on this side, so the superseded
# plain file lingers and every compressed day costs local disk twice (238 MB
# by the time this was noticed). Drop the plain copy only once its .gz
# decompresses to the very same bytes: cmp reads both streams directly, so the
# rm rests on the data itself rather than on a matching filename.
#
# The plain copy can also be a pull taken while the day was still being
# written. If the laptop then misses every pull until the VPS compresses the
# day (three days), rsync never touches that plain file again — the server
# has no <day>.jsonl left to send — and readers keep preferring it
# (config.py's resolve_day_file), so the day stays truncated for good.
# 2026-09-15 sat at 1,783 of 1,920 polls this way. Such a copy is dropped too,
# but only when the .gz passes gzip -t (so its own tail is intact) and starts
# with exactly the plain file's bytes.
#
# Anything else — a corrupt .gz, or bytes that genuinely diverge — keeps the
# plain file and says so on stderr, which is the safe way round.
set -euo pipefail

data_dir="${1:?usage: drop_superseded_plain.sh <data-dir>}"

for gz in "$data_dir"/*/*.jsonl.gz; do
    [[ -f "$gz" ]] || continue  # an unmatched glob stays literal under set -u
    plain="${gz%.gz}"
    [[ -f "$plain" ]] || continue
    if gzip -cd "$gz" | cmp -s - "$plain"; then
        rm "$plain"
        echo "Dropped ${plain}: identical to its .gz"
    # Process substitution rather than a pipe: head closing early SIGPIPEs
    # gzip, which pipefail would report as a mismatch.
    elif gzip -t "$gz" 2>/dev/null \
        && cmp -s <(gzip -cd "$gz" | head -c "$(( $(wc -c < "$plain") ))") "$plain"; then
        rm "$plain"
        echo "Dropped ${plain}: a truncated pull of the day its .gz holds whole"
    else
        echo "Kept ${plain}: its .gz holds different bytes" >&2
    fi
done
