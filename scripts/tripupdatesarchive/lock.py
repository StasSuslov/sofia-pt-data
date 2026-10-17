"""The lock that keeps two packer runs off one directory."""

import contextlib
import fcntl
import os
from collections.abc import Generator
from pathlib import Path


class PackerBusyError(Exception):
    """Another packer run holds the trip updates directory."""


@contextlib.contextmanager
def exclusive(output_dir: Path) -> Generator[None]:
    """Hold a non-blocking flock on output_dir while the block runs.

    systemd never starts a second instance of the unit, but a run by hand
    can overlap the timer's. Two runs on one hour write the same .part, and
    both delete the hour's snapshots after their own read-back passed.

    Raises:
        PackerBusyError: another holder has the lock.
    """
    with contextlib.ExitStack() as stack:
        descriptor = os.open(output_dir, os.O_RDONLY)
        stack.callback(os.close, descriptor)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PackerBusyError(
                f'another packer run holds {output_dir}',
            ) from None
        yield
