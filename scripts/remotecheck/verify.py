"""Compare pending manifests against the VPS and record the verdict."""

import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path

from remotecheck.ssh import RemoteUnreachable

ChecksumMap = dict[str, str]
RemoteChecksumGetter = Callable[[str, list[str]], ChecksumMap]

POLLS_FILE = 'polls_file'


def find_pending_manifests(data_dir: Path) -> list[Path]:
    """Return closed-day manifests with no remote verification recorded yet.

    Args:
        data_dir: Directory holding <date>.manifest.json files.

    Returns:
        The pending manifest paths in date order.
    """
    pending = []
    for manifest_path in sorted(data_dir.glob('????-??-??.manifest.json')):
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('day_in_progress', True):
            continue
        if manifest.get('remote_verified') is not None:
            continue
        pending.append(manifest_path)
    return pending


def _wanted_filenames(manifests: dict[Path, dict]) -> list[str]:
    filenames = set()
    for manifest in manifests.values():
        filenames.add(manifest['data_file'])
        if manifest.get(POLLS_FILE):
            filenames.add(manifest[POLLS_FILE])
    return sorted(filenames)


def _mismatches(
    manifest: dict,
    data_remote: str,
    polls_remote: str | None,
) -> list[str]:
    mismatches = []
    if data_remote != manifest['data_sha256']:
        mismatches.append(
            f'data_sha256 local={manifest["data_sha256"]} remote={data_remote}',
        )
    if manifest.get(POLLS_FILE) and polls_remote != manifest['polls_sha256']:
        mismatches.append(
            f'polls_sha256 local={manifest["polls_sha256"]} '
            f'remote={polls_remote}',
        )
    return mismatches


def _verify_one(
    manifest_path: Path,
    manifest: dict,
    remote_sums: dict[str, str],
) -> tuple[str, bool]:
    data_remote = remote_sums.get(manifest['data_file'])
    polls_needed = manifest.get(POLLS_FILE)
    polls_remote = remote_sums.get(polls_needed) if polls_needed else None

    if data_remote is None or (polls_needed and polls_remote is None):
        return (
            (
                f'{manifest["date"]}: could not verify — file not found on '
                'VPS, will retry next run'
            ),
            False,
        )

    mismatches = _mismatches(manifest, data_remote, polls_remote)
    manifest['remote_verified'] = not mismatches
    manifest['remote_verified_at'] = dt.datetime.now(dt.UTC).isoformat()
    if mismatches:
        manifest['remote_verify_note'] = '; '.join(mismatches)
        line = (
            f'{manifest["date"]}: CHECKSUM MISMATCH — '
            f'{manifest["remote_verify_note"]}'
        )
    else:
        manifest.pop('remote_verify_note', None)
        line = f'{manifest["date"]}: remote checksum verified, matches VPS'

    manifest_path.write_text(
        f'{json.dumps(manifest, indent=2, ensure_ascii=False)}\n',
        encoding='utf-8',
    )
    return line, bool(mismatches)


Verdict = tuple[str, bool]


def _load_pending(data_dir: Path) -> dict[Path, dict]:
    return {
        path: json.loads(path.read_text(encoding='utf-8'))
        for path in find_pending_manifests(data_dir)
    }


def _summarise(verdicts: list[Verdict]) -> tuple[list[str], bool]:
    lines = [verdict[0] for verdict in verdicts]
    return lines, any(verdict[1] for verdict in verdicts)


def verify_pending(
    data_dir: Path,
    remote_dir: str,
    get_remote_checksums: RemoteChecksumGetter,
) -> tuple[list[str], bool]:
    """Verify every pending manifest under data_dir in one batched call.

    The call is get_remote_checksums(remote_dir, filenames) ->
    {filename: hexdigest}.

    Returns (human-readable log lines, whether any real mismatch was found).
    A day is only ever written back to disk once a definitive answer (match
    or mismatch) is reached; an unreachable host or a missing remote file
    leaves the manifest exactly as it was, for the next run to retry.

    Args:
        data_dir: Directory holding <date>.manifest.json files.
        remote_dir: Remote directory holding the day files.
        get_remote_checksums: Reads the remote digests of the given files.

    Returns:
        The log lines and whether any checksum mismatch was found.
    """
    manifests = _load_pending(data_dir)
    if not manifests:
        return ['nothing pending remote verification'], False

    try:
        remote_sums = get_remote_checksums(
            remote_dir,
            _wanted_filenames(manifests),
        )
    except RemoteUnreachable as exc:
        return [
            (
                f'remote verification skipped for {len(manifests)} day(s) — '
                f'VPS unreachable ({exc}); will retry next run'
            ),
        ], False

    verdicts = [
        _verify_one(path, manifest, remote_sums)
        for path, manifest in manifests.items()
    ]
    return _summarise(verdicts)
