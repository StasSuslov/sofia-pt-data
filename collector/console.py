"""Console output of the collector: stdout log lines and stderr warnings."""

import sys
import time


def out(text: str) -> None:
    """Write `text` and a newline to stdout, as print() did."""
    sys.stdout.write(f'{text}\n')


def warn(text: str) -> None:
    """Write a `[WARN]` line to stderr."""
    sys.stderr.write(f'[WARN] {text}\n')


def clock() -> str:
    """Return the local wall-clock time as HH:MM:SS for log line prefixes."""
    return time.strftime('%H:%M:%S')
