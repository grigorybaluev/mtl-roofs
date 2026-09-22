"""Reader for the city's 2D building layer.

``CARTO-BAT-TOIT`` is a roof outline, not a wall footprint, which is what roof
reconstruction wants. It carries **no building identifier**, so identity is
established spatially; see :mod:`mtl_roofs.evaluation.matching`.
"""

from __future__ import annotations

from dataclasses import dataclass

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


def load_footprints(*_args: object, **_kwargs: object) -> object:
    """Load roof outlines for a study area as a GeoDataFrame.

    Not implemented yet: tracked by the "Reprojection and PostGIS loading" issue.
    """
    raise NotImplementedError
