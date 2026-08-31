#!/usr/bin/env python3
"""
Collector cadence constants, shared by collect.py (which writes the data) and
scripts/generate_manifest.py (which audits it against these same numbers).

Deliberately dependency-free: importing collect.py to reach these values would
drag requests and the protobuf bindings into every tool that only reads files
off disk, so a manifest could not be generated on a machine without the
collector's runtime installed.

City-specific constants (BASE_URL, SOFIA_BBOX) still live in collect.py — see
CLAUDE.md D2, which is a stated goal rather than implemented architecture.
"""

DEFAULT_INTERVAL_SEC = 45   # poll every 45 seconds
DEFAULT_HOURS = 24
DEFAULT_TIMEZONE = "Europe/Sofia"
