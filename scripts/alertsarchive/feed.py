"""Parsing and content digest of one GTFS-RT alerts response."""

import hashlib

from google.transit import gtfs_realtime_pb2

_DIGEST_SHOWN = 12


class FeedRejectedError(ValueError):
    """The response is not a usable FeedMessage; nothing may be saved."""


def parse_feed(body: bytes) -> gtfs_realtime_pb2.FeedMessage:
    """Parse a response body and insist on a FeedMessage header.

    Args:
        body: The response bytes as served.

    Returns:
        The parsed FeedMessage.

    Raises:
        FeedRejectedError: The body does not parse or has no header.
    """
    feed = gtfs_realtime_pb2.FeedMessage()
    try:
        feed.ParseFromString(body)
    except Exception as parse_error:
        raise FeedRejectedError(
            f'not a GTFS-RT FeedMessage ({parse_error})',
        ) from parse_error
    if not feed.HasField('header'):
        raise FeedRejectedError(
            'no FeedMessage header (empty or truncated body)',
        )
    return feed


def alerts_digest(feed: gtfs_realtime_pb2.FeedMessage) -> tuple[str, int]:
    """Hash the alerts of a feed, entity ids and header excluded.

    See the archive_alerts module docstring.

    Args:
        feed: A parsed FeedMessage.

    Returns:
        (content hash, number of alerts).
    """
    digests = sorted(
        hashlib.sha256(entity.alert.SerializeToString()).hexdigest()
        for entity in feed.entity
        if entity.HasField('alert')
    )
    joined = '\n'.join(digests).encode('utf-8')
    return hashlib.sha256(joined).hexdigest(), len(digests)


def message(verdict: str, count: int, name: str, digest: str) -> str:
    """Format the one-line stdout verdict for a response.

    Args:
        verdict: 'Saved' or 'No change'.
        count: Number of alerts in the response.
        name: The file saved, or the file the response matches.
        digest: The content hash.

    Returns:
        The line, without the newline.
    """
    head = digest[:_DIGEST_SHOWN]
    if verdict == 'Saved':
        return f'Saved {name}: {count} alerts ({head}...).'
    return f'{verdict}: {count} alerts match {name} ({head}...).'
