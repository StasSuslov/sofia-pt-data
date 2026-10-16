"""The city profile an export credits, and the limits it states."""

from pathlib import Path

from config import REQUIRED_FEEDS, CityProfileError, load_city_for_path
from webexport.errors import warn


def resolve_city(data_dir: Path, slug: str | None) -> dict | None:
    """Load the city profile, or warn and return None."""
    # An export whose city cannot be resolved still runs and says so: the
    # manifest carries attribution: null, and publish_web.py refuses to put a
    # bundle like that on the web. Failing here instead would break nothing
    # but the export itself, while guessing a city would silently publish one
    # operator's licence over another's data.
    try:
        return load_city_for_path(data_dir, slug=slug)
    except CityProfileError as error:
        warn(
            f'WARNING: no city profile resolved ({error}); the export will '
            'carry no attribution and cannot be published as is',
        )
        return None


def _attribution(city: dict) -> dict:
    credit = city['attribution']
    # The city's own name travels with the credit line, so a page can
    # title itself and a published README can name the city without
    # either of them carrying a city constant of its own. feed_licences is
    # narrowed to the feeds this export actually read: the profile
    # describes all of the city's feeds, but a manifest that listed terms
    # for trip_updates or alerts would credit data no bundle here shows.
    return {
        **credit,
        'city': city['name'],
        'city_slug': city['slug'],
        'feed_licences': {
            feed: credit['feed_licences'][feed] for feed in REQUIRED_FEEDS
        },
    }


def city_fields(city: dict | None) -> dict:
    """Return the Shared fields a city profile supplies."""
    if not city:
        return {'attribution': None, 'city_limitations': ()}
    return {
        'attribution': _attribution(city),
        'city_limitations': tuple(city.get('known_limitations', ())),
    }
