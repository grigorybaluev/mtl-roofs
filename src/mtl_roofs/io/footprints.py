"""Reader for the city's 2D building layer.

``CARTO-BAT-TOIT`` is a roof outline, not a wall footprint, which is what roof
reconstruction wants. It carries **no building identifier**, so identity is
established spatially; see :mod:`mtl_roofs.evaluation.matching`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mtl_roofs import CRS_EPSG

#: Layer inside batiments_2d_2016_arrondissements.zip holding roof outlines.
ROOF_LAYER = "CARTO-BAT-TOIT"
#: Roof detail lines (ridges, breaks) - a prior for the topology solver.
DETAIL_LAYER = "CARTO-BAT-DETAIL"
#: Spot elevations.
ELEVATION_LAYER = "CARTO-BAT-COTE"

#: Value of the ``source`` attribute marking features derived from the 2015 LiDAR.
#: Those footprints are not independent of this project's input and are reported
#: separately in the evaluation.
LIDAR_DERIVED_SOURCE = "LiDAR aérien 2015"


@dataclass(frozen=True, slots=True)
class FootprintProvenance:
    """Per-feature provenance, read from the layer's own attributes."""

    method: str
    source: str
    updated: str

    @property
    def is_lidar_derived(self) -> bool:
        """Whether this footprint was itself derived from the 2015 LiDAR."""
        return LIDAR_DERIVED_SOURCE in self.source


def footprint_id(centroid_x: float, centroid_y: float) -> str:
    """The project's stable building id for a footprint, from its centroid (ADR 0005).

    ``fp-<E>-<N>``: the centroid in EPSG:2950, in decimetres, rounded half up. It depends
    only on the source geometry, so it is the same on every run.
    """
    return f"fp-{math.floor(centroid_x * 10 + 0.5)}-{math.floor(centroid_y * 10 + 0.5)}"


def load_footprints(
    path: Path, bbox: tuple[float, float, float, float] | None = None
) -> Any:  # geopandas.GeoDataFrame; geopandas is untyped
    """Load ``CARTO-BAT-TOIT`` roof outlines as a GeoDataFrame, one row per footprint.

    Args:
        path: The city's ``batiments_2d_2016_arrondissements.zip``, read in place.
        bbox: ``(xmin, ymin, xmax, ymax)`` in EPSG:2950; only intersecting features load.

    Returns:
        Columns ``footprint_id``, ``lidar_derived``, ``methode``, ``source``, ``MAJ`` and
        ``geometry``, sorted by id. The layer's ``.prj`` names the CRS
        ``NAD_1983_CRS98_MTM_8``; it is asserted as EPSG:2950 rather than inferred.

    The source holds a few polygons twice with different attributes. Records sharing an
    id are one footprint, flagged LiDAR-derived if any of them is (ADR 0005).
    """
    import geopandas as gpd

    frame = gpd.read_file(f"zip://{path}!{ROOF_LAYER}.shp", bbox=bbox)
    frame = frame.set_crs(CRS_EPSG, allow_override=True)
    centroids = frame.geometry.centroid
    frame["footprint_id"] = [
        footprint_id(x, y) for x, y in zip(centroids.x, centroids.y, strict=True)
    ]
    frame["lidar_derived"] = frame["source"].str.contains(LIDAR_DERIVED_SOURCE, regex=False)
    lidar_any = frame.groupby("footprint_id")["lidar_derived"].transform("any")
    frame["lidar_derived"] = lidar_any
    frame = frame.drop_duplicates("footprint_id", keep="first")
    columns = ["footprint_id", "lidar_derived", "methode", "source", "MAJ", "geometry"]
    return frame[columns].sort_values("footprint_id").reset_index(drop=True)
