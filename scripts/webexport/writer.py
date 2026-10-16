"""Writing one bundle (a schedule period, or one day) to disk."""

import shutil
from dataclasses import dataclass
from pathlib import Path

from webexport.constants import MANIFEST_NAME, TYPICAL_MODE
from webexport.fileio import write_compact, write_pretty
from webexport.layout import build_geometry
from webexport.manifest import build_manifest
from webexport.model import Bundle, ManifestInputs, Shared
from webexport.report import (
    GEOMETRY_NAME,
    SLOTS_DIR_NAME,
    report_counts,
    report_sizes,
)
from webexport.thresholds import apply_threshold, segment_pairs
from webexport.timeslots import build_timeslot_files, build_typical_kmh


@dataclass(frozen=True, kw_only=True)
class _Thresholded:
    """The bins before and after the min_samples threshold."""

    retained: dict
    pairs_before: set
    pairs_after: set
    bins_before: int
    bins_after: int


@dataclass(frozen=True, kw_only=True)
class _Built:
    """Everything computed in memory before the output directory is touched."""

    thresholded: _Thresholded
    geometry: dict
    missing_shapes: int
    timeslot_files: dict


def _threshold(bins: dict, min_samples: int) -> _Thresholded:
    retained, bins_before, bins_after = apply_threshold(bins, min_samples)
    return _Thresholded(
        retained=retained,
        pairs_before=segment_pairs(bins),
        pairs_after=segment_pairs(retained),
        bins_before=bins_before,
        bins_after=bins_after,
    )


def _build(bins: dict, shared: Shared, mode: str) -> _Built:
    thresholded = _threshold(bins, shared.min_samples)
    geometry, index_of, missing_shapes = build_geometry(
        thresholded.pairs_after,
        shared.shapes_by_key,
        shared.route_info,
        shared.shape_ids_by_key,
    )
    timeslot_files = build_timeslot_files(thresholded.retained, index_of)

    # Typical-weekday bundles only. The thresholds the frontend compares
    # against typical_kmh were calibrated on a period's medians; a single
    # day's medians are a different object, and carrying the array into a day
    # bundle would invite exactly the reuse of a number measured elsewhere
    # that CLAUDE.md section 9 keeps a tally of.
    if mode == TYPICAL_MODE:
        geometry['typical_kmh'] = build_typical_kmh(
            timeslot_files,
            len(geometry['shape_idx']),
        )
    return _Built(
        thresholded=thresholded,
        geometry=geometry,
        missing_shapes=missing_shapes,
        timeslot_files=timeslot_files,
    )


def _write_data_files(out_dir: Path, built: _Built) -> None:
    # This script owns everything under out_dir once it exists, so rebuild
    # it from scratch rather than overwrite in place — a rerun with a
    # tighter --min-samples, or a day that lost coverage since the last
    # export, would otherwise leave stale timeslot files on disk that the
    # freshly written manifest.json's "timeslots" list doesn't mention.
    shutil.rmtree(out_dir, ignore_errors=True)
    slots_dir = out_dir / SLOTS_DIR_NAME
    slots_dir.mkdir(parents=True)
    for slot, payload in built.timeslot_files.items():
        write_compact(slots_dir / f'{slot.replace(":", "")}.json', payload)
    write_compact(out_dir / GEOMETRY_NAME, built.geometry)


def _manifest_inputs(
    built: _Built,
    shared: Shared,
    bundle: Bundle,
) -> ManifestInputs:
    geometry = built.geometry
    thresholded = built.thresholded
    return ManifestInputs(
        mode=bundle.mode,
        min_samples=shared.min_samples,
        bins_before=thresholded.bins_before,
        bins_after=thresholded.bins_after,
        pairs_before=len(thresholded.pairs_before),
        pairs_after=len(thresholded.pairs_after),
        missing_shapes=built.missing_shapes,
        timeslot_labels=list(built.timeslot_files.keys()),
        source=bundle.source,
        days_processed=bundle.days_processed,
        days_in_median=bundle.days_in_median,
        incomplete_days=bundle.incomplete_days,
        shapes_observed=len({sid for sid, _ in thresholded.pairs_before}),
        shapes_written=len(geometry['shape_keys']),
        total_static_shapes=len(shared.shapes_by_key),
        multi_route_shapes=sum(
            1 for ids in geometry['shape_route_ids'] if len(ids) > 1
        ),
        attribution=shared.attribution,
        city_limitations=shared.city_limitations,
        schedule_period=bundle.schedule_period,
    )


def write_export(
    out_dir: Path,
    bins: dict,
    shared: Shared,
    bundle: Bundle,
) -> dict:
    """Write threshold, geometry, timeslot files and manifest into out_dir.

    One bundle is one median: a schedule period of the typical weekday, or a
    single day for the day switcher. Both callers reach the files through
    here, so the format cannot drift between them.

    Args:
        out_dir: The bundle directory, rebuilt from scratch.
        bins: The speed bins before the min_samples threshold.
        shared: What every bundle of the run has in common.
        bundle: What distinguishes this bundle.

    Returns:
        The manifest it wrote.
    """
    built = _build(bins, shared, bundle.mode)
    _write_data_files(out_dir, built)
    manifest = build_manifest(_manifest_inputs(built, shared, bundle))
    write_pretty(out_dir / MANIFEST_NAME, manifest)
    thresholded = built.thresholded
    report_sizes(out_dir, built.geometry)
    report_counts(
        (thresholded.bins_before, thresholded.bins_after),
        (len(thresholded.pairs_before), len(thresholded.pairs_after)),
        shared.min_samples,
        built.missing_shapes,
    )
    return manifest
