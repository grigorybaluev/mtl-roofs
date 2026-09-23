"""Per-footprint point clipping and roof filtering.

The first stage of reconstruction: take the points of one footprint and keep only
those that can belong to its roof. What each LiDAR class contributes was measured on
tile ``292-5035`` and the fixtures, not assumed; see ``docs/data-sources.md``.

Every class has an explicit policy in :data:`CLASS_POLICY`. A class code missing from
it raises instead of falling through to a default: an undocumented class in a new
tile has to get a decision, as class 28 did.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import numpy.typing as npt
import shapely
from scipy.spatial import cKDTree

from mtl_roofs.io.lidar import PointSet


class ClassPolicy(Enum):
    """What the roof filter does with the points of one LiDAR class."""

    KEEP = "keep"
    KEEP_IF_ELEVATED = "keep-if-elevated"
    DROP = "drop"


CLASS_POLICY: dict[int, ClassPolicy] = {
    # Measured: no class-1 point inside the fixtures' reference roofs is at roof height;
    # the class is ground-level surface left unlabelled. Elevated ones would be roof,
    # so the height test costs nothing and protects other tiles.
    1: ClassPolicy.KEEP_IF_ELEVATED,
    2: ClassPolicy.DROP,  # ground; still used as the height datum
    3: ClassPolicy.DROP,  # low vegetation
    4: ClassPolicy.DROP,  # medium vegetation
    5: ClassPolicy.DROP,  # high vegetation
    6: ClassPolicy.KEEP,  # building
    7: ClassPolicy.DROP,  # low point (noise)
    8: ClassPolicy.DROP,  # reserved; never observed
    # Undocumented, 5.45% of points. Mostly low clutter (vehicles, fences, hedges),
    # but also the only returns from some lower roof sections, the lowest 1.58 m above
    # local ground. Above 1.5 m it keeps all on-roof class-28 points of the fixtures and
    # 58% of the clutter (~0.8% of retained points), which plane detection treats as
    # outliers. 2.5 m would lose 25% of the roof points. Decided in #17.
    28: ClassPolicy.KEEP_IF_ELEVATED,
}

GROUND_CLASS = 2


class ClipStatus(Enum):
    """Outcome of clipping one footprint. Values match the failure log statuses."""

    OK = "ok"
    TOO_FEW_POINTS = "too-few-points"


@dataclass(frozen=True, slots=True)
class ClipParams:
    """Clipping and filtering parameters.

    Attributes:
        buffer_m: Grow the footprint by this much before clipping. 0 by default:
            on the fixtures it captures every building point inside the reference
            roofs, and any positive buffer only adds neighbours' roofs and walls.
        min_height_m: Height above ground a ``KEEP_IF_ELEVATED`` point must exceed.
        ground_neighbours: Ground points whose median height is the local ground.
        min_points: Fewer retained points than this is ``too-few-points``. Matches
            the plane detector's default minimum inlier count.
    """

    buffer_m: float = 0.0
    min_height_m: float = 1.5
    ground_neighbours: int = 8
    min_points: int = 50


@dataclass(frozen=True, slots=True)
class ClipResult:
    """Roof points of one footprint, with what the evaluation breakdown needs.

    Attributes:
        roof: Retained points; ``roof.xyz`` goes to plane detection.
        status: ``ok`` or ``too-few-points``.
        density: Retained points per m² of footprint.
        kept_by_class: Retained point count per class code.
        ground_available: Whether ground points were found to measure heights
            against. Without them, ``KEEP_IF_ELEVATED`` classes are dropped.
    """

    roof: PointSet
    status: ClipStatus
    density: float
    kept_by_class: dict[int, int] = field(default_factory=dict)
    ground_available: bool = True

    @property
    def n_points(self) -> int:
        """Number of retained roof points."""
        return len(self.roof)


def clip_footprint(
    points: PointSet, footprint: shapely.Polygon, params: ClipParams | None = None
) -> ClipResult:
    """Keep the points of ``footprint`` that can belong to its roof.

    Args:
        points: Points around the footprint, including some ground around it
            (the fixtures carry 2 m); ground outside the footprint is only used to
            measure height.
        footprint: The ``CARTO-BAT-TOIT`` polygon, in EPSG:2950.
        params: Defaults to :class:`ClipParams`.

    Raises:
        ValueError: if ``points`` holds a class code with no entry in
            :data:`CLASS_POLICY`.
    """
    p = params or ClipParams()
    cls = points.classification
    unknown = sorted(set(np.unique(cls).tolist()) - CLASS_POLICY.keys())
    if unknown:
        msg = f"no roof-filter policy for LiDAR class(es) {unknown}; add them to CLASS_POLICY"
        raise ValueError(msg)

    region = footprint.buffer(p.buffer_m) if p.buffer_m else footprint
    x, y, z = points.xyz[:, 0], points.xyz[:, 1], points.xyz[:, 2]
    inside = shapely.intersects_xy(region, x, y)

    policy = np.array([CLASS_POLICY[int(c)].value for c in cls], dtype=object)
    keep = inside & (policy == ClassPolicy.KEEP.value)
    conditional = inside & (policy == ClassPolicy.KEEP_IF_ELEVATED.value)

    ground = cls == GROUND_CLASS
    ground_available = bool(ground.any())
    if ground_available and conditional.any():
        height = z[conditional] - _ground_height(
            points.xyz[ground], points.xyz[conditional], p.ground_neighbours
        )
        idx = np.flatnonzero(conditional)
        keep[idx[height > p.min_height_m]] = True

    roof = PointSet(points.xyz[keep], cls[keep])
    codes, counts = np.unique(roof.classification, return_counts=True)
    status = ClipStatus.OK if len(roof) >= p.min_points else ClipStatus.TOO_FEW_POINTS
    return ClipResult(
        roof=roof,
        status=status,
        density=len(roof) / footprint.area if footprint.area > 0 else 0.0,
        kept_by_class={int(c): int(n) for c, n in zip(codes, counts, strict=True)},
        ground_available=ground_available,
    )


def _ground_height(
    ground: npt.NDArray[np.float64], query: npt.NDArray[np.float64], k: int
) -> npt.NDArray[np.float64]:
    """Local ground elevation under each query point: median of its nearest ground points."""
    k = min(k, len(ground))
    _, idx = cKDTree(ground[:, :2]).query(query[:, :2], k=k)
    idx = np.asarray(idx).reshape(len(query), k)
    heights: npt.NDArray[np.float64] = np.median(ground[idx, 2], axis=1)
    return heights
