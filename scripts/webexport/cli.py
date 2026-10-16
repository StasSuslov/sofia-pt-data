"""Entry point of export_web.py: parse, resolve the city, export."""

import argparse

from webexport.arguments import build_parser
from webexport.city import city_fields, resolve_city
from webexport.day import export_day
from webexport.model import Run, Shared
from webexport.static import load_static_sources
from webexport.typical import export_typical


def _load_shared(args: argparse.Namespace, fields: dict) -> tuple:
    shapes_by_key, route_info, shape_ids_by_key, static_paths = (
        load_static_sources(args.static_source)
    )
    shared = Shared(
        min_samples=args.min_samples,
        shapes_by_key=shapes_by_key,
        route_info=route_info,
        shape_ids_by_key=shape_ids_by_key,
        **fields,
    )
    return shared, [path.name for path in static_paths]


def make_run(args: argparse.Namespace) -> Run:
    """Resolve the city and load the static feed(s) for this invocation."""
    fields = city_fields(resolve_city(args.data_dir, args.city))
    shared, static_feed_names = _load_shared(args, fields)
    return Run(
        shared=shared,
        data_dir=args.data_dir,
        processed_dir=args.processed_dir or (args.data_dir / 'processed'),
        output_root=args.output_dir or (args.data_dir / 'web'),
        static_feed_names=static_feed_names,
    )


def main(description: str) -> None:
    """Run the export; `description` is the module docstring."""
    args = build_parser(description).parse_args()
    run = make_run(args)
    if args.day:
        export_day(run, args.day)
    else:
        export_typical(run)
