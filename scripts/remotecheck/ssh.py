"""One batched ssh call that reads the VPS's own SHA256 of day files."""

import subprocess  # noqa: S404  fixed-argv ssh call, no shell, no user input
from pathlib import Path

SSH_CONNECT_TIMEOUT_SEC = 10
SSH_CALL_TIMEOUT_SEC = 30
SSH_CONNECTION_FAILED_EXIT = 255


class RemoteUnreachable(Exception):  # noqa: N818  name is imported by tests
    """The VPS couldn't be reached (or ssh itself failed).

    As opposed to the VPS answering with a checksum that doesn't match. Never
    carries the raw ssh host or stderr text: those can contain the VPS
    address, and this exception's message is the kind of thing that ends up
    in logs/fetch.log.
    """


def _remote_command(remote_dir: str, filenames: list[str]) -> str:
    names_quoted = ' '.join(f'"{name}"' for name in filenames)
    # stderr redirected on the remote side: nothing here should print to it
    # given the -f guards, but a permission error or a corrupt .gz must not
    # abort the batch or leak a remote path into our own stderr.
    return (
        f'for f in {names_quoted}; do '
        f'p="{remote_dir}/$f"; '
        f'if [ -f "$p" ]; then sha256sum "$p"; '
        f'elif [ -f "$p.gz" ]; then h=$(gzip -dc "$p.gz" | sha256sum '
        f'| cut -d\' \' -f1); echo "$h  $p"; '
        f'fi; done 2>/dev/null'
    )


def _parse_checksums(stdout: str) -> dict[str, str]:
    checksums = {}
    for line in stdout.splitlines():
        digest, _, path = line.partition('  ')
        if digest and path:
            checksums[Path(path.strip()).name] = digest
    return checksums


def ssh_remote_sha256sums(
    vps_host: str,
    vps_key: str,
    remote_dir: str,
    filenames: list[str],
) -> dict[str, str]:
    """Read the remote SHA256 of every requested file in one ssh call.

    One ssh call, one remote `for` loop over every requested filename —
    verifying N pending days costs one round trip, not N. Returns
    {filename: hexdigest} for whichever of the requested files still exist
    on the remote side (plain or gzipped); a name simply absent from the
    result means that file wasn't found there in either form (manual
    cleanup, or a transfer that hasn't happened yet), which the caller
    treats as "can't verify yet", not as a mismatch.

    `filenames` are always the UNCOMPRESSED names — manifest["data_file"] /
    manifest["polls_file"] never carry a .gz suffix, per CLAUDE.md's
    provenance invariant. deploy/sofia-compress.service may have gzipped
    that day on the VPS after the manifest was written and before this runs,
    so each name is checked plain first, falling back to decompress-then-
    hash of "<name>.gz" — either way the digest is over the same
    uncompressed bytes generate_manifest.py hashed locally, and the emitted
    line always names the uncompressed path so parsing below can't tell
    which branch ran.

    Args:
        vps_host: ssh destination, user@host.
        vps_key: Path to the ssh private key.
        remote_dir: Remote directory holding the day files.
        filenames: Uncompressed file names to hash.

    Returns:
        Hex digests keyed by file name, for the files found.

    Raises:
        RemoteUnreachable: ssh failed to connect, timed out or did not start.
    """
    try:
        completed = subprocess.run(  # noqa: S603  fixed argv, no shell
            [  # noqa: S607  PATH lookup of ssh is intended
                'ssh',
                '-i',
                vps_key,
                '-o',
                'BatchMode=yes',
                '-o',
                f'ConnectTimeout={SSH_CONNECT_TIMEOUT_SEC}',
                vps_host,
                _remote_command(remote_dir.rstrip('/'), filenames),
            ],
            capture_output=True,
            text=True,
            timeout=SSH_CALL_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RemoteUnreachable(type(exc).__name__) from exc

    # ssh reserves exit code 255 for its OWN connection-level failures (auth,
    # timeout, refused, DNS) rather than the remote command's exit status —
    # that's the one code that means "never actually ran". A non-zero exit
    # otherwise just means sha256sum itself was unhappy (e.g. a missing
    # file), and stdout still holds valid lines for every file that WAS
    # found, so that case falls through to the normal parsing below.
    if completed.returncode == SSH_CONNECTION_FAILED_EXIT:
        raise RemoteUnreachable('ssh connection failed')

    return _parse_checksums(completed.stdout)
