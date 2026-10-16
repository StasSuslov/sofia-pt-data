"""Download and validation of a GTFS Static candidate."""

import csv
import io
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

DEFAULT_TIMEOUT_SEC = 60

# The files segment_speeds.py's load_static()/load_feed_info() and
# export_web.py's load_route_info() actually read. Absence of any one of
# these means a truncated download or an HTML error page served with a 200
# status, not a usable snapshot.
REQUIRED_MEMBERS = ('trips.txt', 'shapes.txt', 'stops.txt', 'routes.txt')


def _fetch_into(out_fd: int, url: str, user_agent: str, timeout: int) -> None:
    with os.fdopen(out_fd, 'wb') as out:
        # URL from the city profile or --url: operator input, not user data.
        request = Request(  # noqa: S310
            url,
            headers={'User-Agent': user_agent},
        )
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            shutil.copyfileobj(response, out)


def download_to_temp(
    url: str,
    dest_dir: Path,
    user_agent: str,
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> Path:
    """Download `url` into a hidden temp file inside dest_dir.

    The temp name starts with "." and doesn't match SNAPSHOT_NAME_RE, so it
    can never be mistaken for a real snapshot by this script or by
    segment_speeds.py's find_static_snapshots() if a run is interrupted
    before cleanup. Written inside dest_dir, not the system temp directory,
    so the final save is a same-filesystem os.replace() rather than a
    cross-device copy.

    Any failure, network or otherwise, removes the temp file before the
    exception propagates. Nothing calling this function needs to remember
    to clean up after it.

    Args:
        url: The feed URL.
        dest_dir: Directory for the temp file, created if missing.
        user_agent: The city profile's User-Agent.
        timeout: Request timeout in seconds.

    Returns:
        The temp file's path.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_fd, tmp_name = tempfile.mkstemp(
        prefix='.gtfs_static_download_',
        suffix='.zip.part',
        dir=dest_dir,
    )
    tmp_path = Path(tmp_name)
    try:
        _fetch_into(out_fd, url, user_agent, timeout)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return tmp_path


def _member_names(path: Path) -> set:
    try:
        with zipfile.ZipFile(path) as archive:
            return set(archive.namelist())
    except zipfile.BadZipFile as bad_zip:
        reason = f'not a valid zip file ({bad_zip})'
        raise ValueError(reason) from bad_zip


def validate_feed_zip(path: Path) -> None:
    """Check that `path` is a usable GTFS Static snapshot.

    Callers must fail loudly on the exception rather than archive the file
    anyway; see the archive_static_feed module docstring.

    Args:
        path: The candidate zip.

    Raises:
        ValueError: Not a valid zip at all, or missing one of
            REQUIRED_MEMBERS, with a human-readable reason.
    """
    names = _member_names(path)
    missing = [member for member in REQUIRED_MEMBERS if member not in names]
    if missing:
        names_list = ', '.join(missing)
        raise ValueError(f'missing required file(s): {names_list}')


def _first_feed_info_row(zip_path: Path) -> dict:
    with (
        zipfile.ZipFile(zip_path) as archive,
        archive.open(
            'feed_info.txt',
        ) as member,
    ):
        rows = csv.DictReader(io.TextIOWrapper(member, encoding='utf-8'))
        return next(rows, {})


def load_feed_info(zip_path: Path) -> dict:
    """Best-effort feed_info.txt row for stamping the manifest.

    Absent in some GTFS feeds and not required by validate_feed_zip(), so a
    missing file or an empty table returns {} rather than raising.
    """
    try:
        return _first_feed_info_row(zip_path)
    except (KeyError, zipfile.BadZipFile, StopIteration):
        return {}
