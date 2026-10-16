"""--discover: probe the city profile's feed URLs."""

import requests

from collector.console import out
from collector.feed import parse_feed

PROBE_TIMEOUT_SEC = 10


class _NotGtfsRtError(Exception):
    """The URL answered, but not with a GTFS-RT protobuf."""

    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


def _probe_feed(session: requests.Session, url: str) -> tuple[int, int, int]:
    """Return (HTTP status, entity count, vehicle count) for a feed URL."""
    response = session.get(url, timeout=PROBE_TIMEOUT_SEC)
    try:
        # Try to parse as protobuf
        feed = parse_feed(response.content)
    except Exception as error:
        raise _NotGtfsRtError(response.status_code) from error
    vehicles = sum(1 for entity in feed.entity if entity.HasField('vehicle'))
    return response.status_code, len(feed.entity), vehicles


def _report_bad_feed(name: str, url: str, status) -> bool:
    out(
        f'  ⚠️   {name}: {url}  →  HTTP {status}, not valid GTFS-RT protobuf',
    )
    return False


def _report_feed(session: requests.Session, name: str, url: str) -> bool:
    """Probe one feed, print the verdict, and say whether it answered."""
    try:
        status, entities, vehicles = _probe_feed(session, url)
    except requests.RequestException as error:
        out(f'  ❌  {name}: {url}  →  {error}')
        return False
    except _NotGtfsRtError as error:
        return _report_bad_feed(name, url, error.status)
    except Exception:
        return _report_bad_feed(name, url, '???')
    out(f'  ✅  {name}: {url}')
    out(f'      HTTP {status} | {entities} entities | {vehicles} vehicles')
    out(f'      → collect with: --url {url}')
    return True


def discover_endpoint(session: requests.Session, city: dict) -> None:
    """Probe the city profile's own feed URLs and report which respond.

    A feed answers when it returns parseable GTFS-RT data. Run this when
    adding a city, or to re-verify a feed that was working when the profile
    was written.

    The URLs come from the profile whole, never assembled from a base plus a
    path: a domain that resolves says nothing about a path that exists, and
    section 7 of CLAUDE.md forbids guessing the second from the first. A feed
    that a probe cannot find belongs in the profile only after someone finds
    the real URL and writes it down.
    """
    slug = city['slug']
    out(f"Probing {city['name']}'s GTFS-RT feeds ({slug})...\n")

    answers = [
        _report_feed(session, name, url)
        for name, url in city['feeds'].items()
        if name != 'static'  # a zip, not protobuf: parsing it would only fail
    ]

    if any(answers):
        out(
            f'\nThese are the URLs already in cities/{slug}.json; '
            'collection uses them by default, and --url overrides one for a '
            'single run.',
        )
    else:
        out(
            f'\nNo feed in cities/{slug}.json answered with GTFS-RT. Check the '
            "agency's open-data portal for the current URLs, or watch what "
            "the operator's own site requests, and update the profile with "
            'what you find.',
        )


def run_discovery(city: dict) -> None:
    """Run discover_endpoint() on a throwaway session for `city`."""
    session = requests.Session()
    session.headers['User-Agent'] = city['user_agent']
    discover_endpoint(session, city)
    session.close()
