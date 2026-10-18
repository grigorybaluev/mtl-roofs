"""Batch reconstruction of a study area (#21).

Every footprint of the area gets a row, failures included: a footprint that vanished
from the output would be a bug, not a filter. The run is

* **resumable:** each footprint is committed as soon as it is done, and a re-run
  skips footprints that already have a row (``--force`` starts over);
* **isolated:** an exception on one footprint is recorded as ``error`` with its
  message, and the run goes on;
* **logged:** wall-clock time, peak memory and the status counts go to
  ``data/processed/<area>/runs/<run_id>.json``, and every row to
  ``data/processed/<area>/reconstructions.csv``.
"""

from __future__ import annotations

import csv
import json
import logging
import resource
import sys
import time
import traceback
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import shapely

from mtl_roofs.geometry.clip import CLASS_POLICY, GROUND_CLASS, ClassPolicy
from mtl_roofs.io.lidar import PointIndex, PointSet, tile_member
from mtl_roofs.pipeline import PipelineParams, Reconstruction, Status, reconstruct_footprint

log = logging.getLogger(__name__)

#: Context around a footprint given to the pipeline, as in the fixtures: ground there
#: is what the clipper measures roof heights against.
CONTEXT_M = 2.0

#: Classes the pipeline can use. Vegetation and noise are dropped at load, which
#: halves the memory an area needs and changes no result.
USED_CLASSES = frozenset(
    c for c, policy in CLASS_POLICY.items() if policy is not ClassPolicy.DROP
) | {GROUND_CLASS}

Footprint = tuple[str, bool, Any]  # footprint_id, lidar_derived, polygon
Save = Callable[[str, Reconstruction, float], None]


@dataclass(slots=True)
class RunLog:
    """What one batch run did.

    Attributes:
        run_id: UTC start time, which names the log file.
        area: Study area.
        n_footprints: Footprints in the area.
        n_skipped: Footprints already reconstructed by an earlier run.
        n_processed: Footprints this run reconstructed.
        status_counts: Statuses of this run's footprints.
        seconds: Wall-clock time of the run.
        load_seconds: Of which, reading the LiDAR.
        peak_memory_mb: Peak resident memory of the process.
        n_points: LiDAR points loaded.
        beyond_coverage: Footprints reaching outside the area's LiDAR tiles.
    """

    run_id: str
    area: str
    n_footprints: int = 0
    n_skipped: int = 0
    n_processed: int = 0
    status_counts: dict[str, int] = field(default_factory=dict)
    seconds: float = 0.0
    load_seconds: float = 0.0
    peak_memory_mb: float = 0.0
    n_points: int = 0
    beyond_coverage: list[str] = field(default_factory=list)


def footprint_points(index: PointIndex, polygon: Any, context_m: float = CONTEXT_M) -> PointSet:
    """The points within ``context_m`` of a footprint."""
    xmin, ymin, xmax, ymax = polygon.bounds
    window = index.query(xmin - context_m, ymin - context_m, xmax + context_m, ymax + context_m)
    region = shapely.buffer(polygon, context_m)
    shapely.prepare(region)
    inside = shapely.contains_xy(region, window.xyz[:, 0], window.xyz[:, 1])
    return PointSet(window.xyz[inside], window.classification[inside])


def process(
    footprints: Iterable[Footprint],
    index: PointIndex,
    params: PipelineParams,
    save: Save,
    on_done: Callable[[str, Status], None] | None = None,
) -> Counter[str]:
    """Reconstruct each footprint and hand the result to ``save``.

    An exception inside the pipeline becomes an ``error`` result with the exception
    as its reason; the loop goes on. An exception in ``save`` is not caught: losing
    the store is not a per-building failure.
    """
    counts: Counter[str] = Counter()
    for footprint_id, _, polygon in footprints:
        start = time.perf_counter()
        try:
            result = reconstruct_footprint(footprint_points(index, polygon), polygon, params)
        except Exception as exc:  # one building must not stop the run
            log.error("%s failed:\n%s", footprint_id, traceback.format_exc())
            result = Reconstruction(Status.ERROR, reason=f"{type(exc).__name__}: {exc}")
        save(footprint_id, result, time.perf_counter() - start)
        counts[result.status.value] += 1
        if on_done is not None:
            on_done(footprint_id, result.status)
    return counts


def lidar_paths(raw: Path, tiles: Sequence[str]) -> list[Path]:
    """The local files of a study area's LiDAR tiles.

    Raises:
        FileNotFoundError: naming the tiles not downloaded yet.
    """
    paths = [raw / tile_member(t) for t in tiles]
    missing = [p.name for p in paths if not p.exists()]
    if missing:
        msg = f"LiDAR tiles not downloaded: {', '.join(missing)}; run `mtl-roofs data fetch`"
        raise FileNotFoundError(msg)
    return paths


def beyond_coverage(footprints: Sequence[Footprint], tiles: Sequence[str]) -> list[str]:
    """Footprints reaching outside a set of 1 km tiles, so partly without LiDAR.

    The area's footprints are those intersecting its bbox, so a few cross its edge;
    their points stop at the last tile. They are still reconstructed, and listed in
    the run log.
    """
    covered = shapely.union_all(
        [
            shapely.box(1000 * x, 1000 * y, 1000 * (x + 1), 1000 * (y + 1))
            for x, y in (map(int, t.split("-")) for t in tiles)
        ]
    )
    shapely.prepare(covered)
    return [fid for fid, _, polygon in footprints if not covered.contains(polygon)]


def covering_bbox(
    footprints: Sequence[Footprint], margin: float = CONTEXT_M
) -> tuple[float, float, float, float]:
    """Bounds of a set of footprints, grown by ``margin``."""
    bounds = np.array([polygon.bounds for _, _, polygon in footprints])
    return (
        float(bounds[:, 0].min() - margin),
        float(bounds[:, 1].min() - margin),
        float(bounds[:, 2].max() + margin),
        float(bounds[:, 3].max() + margin),
    )


def peak_memory_mb() -> float:
    """Peak resident set size of this process, in MB."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Bytes on macOS, kilobytes on Linux.
    return peak / (1 << 20) if sys.platform == "darwin" else peak / (1 << 10)


def write_csv(path: Path, columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    """Write rows to CSV; list and tuple values are joined with ``;``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for row in rows:
            writer.writerow(
                [";".join(map(str, v)) if isinstance(v, list | tuple) else v for v in row]
            )


def write_log(path: Path, run: RunLog) -> None:
    """Write the run log as JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(run), indent=2) + "\n")


def new_run_id() -> str:
    """A run id: the UTC start time, sortable and safe in a file name."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
