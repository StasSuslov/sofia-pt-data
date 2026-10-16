"""Messages and fatal errors of the export."""

import sys
from typing import NoReturn


def warn(message: str) -> None:
    """Write `message` to stderr."""
    sys.stderr.write(f'{message}\n')


def fail(message: str) -> NoReturn:
    """Write `message` to stderr and exit with status 1."""
    warn(message)
    sys.exit(1)
