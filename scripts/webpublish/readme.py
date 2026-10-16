"""The README written into the published repository."""

_README_TEMPLATE = """\
# {city} public transport — map

Generated output. This site is built and force-pushed by
`scripts/publish_web.py` in the project repository; it holds a single commit
and is rewritten on every publish, so nothing here is edited by hand.

It publishes a rolling window of the most recent observed days plus the
typical-weekday medians for every schedule period in the archive. The
complete archive is published as a dataset record with a DOI.

- Code and methodology: https://github.com/StasSuslov/sofia-pt-data
- Code DOI (concept): 10.5281/zenodo.22256653
- Dataset DOI (concept): 10.5281/zenodo.22285128

Transit data: {source_name} ({feed_description}),
operated by {operator}.
{source_url}

The feeds do not share one licence, so they are named apart:
{feed_licences}

Licences: code MIT, everything published here {licence}, because the
segment geometry is derived from the schedule feed.
"""


def feed_licence_lines(attribution: dict) -> str:
    """One line per feed, because the feeds disagree.

    Sofia's vehicle positions take the portal's default CC BY 4.0 while its
    schedule feed overrides it with share-alike. A single licence string had
    to pick one of them, and for four weeks it picked the wrong one.
    """
    return '\n'.join(
        f'- {feed.replace("_", " ")}: {licence}'
        for feed, licence in attribution['feed_licences'].items()
    )


def readme_text(attribution: dict) -> str:
    """The published repository's README.

    Named after the city whose data it actually holds and crediting that
    city's feed under that feed's licence.

    Written from the export's own manifests rather than from a constant
    here: a constant would keep saying Sofia, and one licence, over whatever
    tree was handed to it.
    """
    return _README_TEMPLATE.format(
        city=attribution.get('city', 'Public'),
        source_name=attribution['source_name'],
        feed_description=attribution.get('feed_description', 'GTFS/GTFS-RT'),
        operator=attribution.get('operator', 'the local operator'),
        source_url=attribution.get('source_url', ''),
        feed_licences=feed_licence_lines(attribution),
        licence=attribution['licence'],
    )
