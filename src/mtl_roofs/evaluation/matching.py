"""Assigning reference buildings to footprints.

The footprint layer carries no building identifier and the CityGML model uses its
own ``gml:id``, so there is no join key between the two. Assignment is therefore
spatial, and its failure rate is a reported metric rather than a hidden detail.

Each reference building goes to the footprint holding the largest share of its roof
outline, if that share is at least one half; ties go to the lower footprint id
(ADR 0005). One footprint can hold several reference buildings: a semi-detached pair
or a row is one ``CARTO-BAT-TOIT`` polygon.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import shapely

#: Smallest share of a roof outline a footprint must hold to be assigned it.
MIN_SHARE = 0.5


def iou(area_a: float, area_b: float, area_intersection: float) -> float:
    """Intersection over union of two areas.

    Args:
        area_a: Area of the first polygon.
        area_b: Area of the second polygon.
        area_intersection: Area common to both.

    Raises:
        ValueError: if any area is negative, or the intersection exceeds either area.
    """
    if min(area_a, area_b, area_intersection) < 0:
        msg = "areas must be non-negative"
        raise ValueError(msg)
    if area_intersection > min(area_a, area_b) + 1e-9:
        msg = f"intersection {area_intersection} exceeds a constituent area"
        raise ValueError(msg)
    union = area_a + area_b - area_intersection
    if union <= 0:
        return 0.0
    return area_intersection / union


@dataclass(frozen=True, slots=True)
class Assignment:
    """The footprint a reference building is scored against.

    Attributes:
        footprint_id: The footprint holding the largest share of the roof outline.
        share: That share, in ``[MIN_SHARE, 1]``.
    """

    footprint_id: str
    share: float


def assign_buildings(
    footprints: Mapping[str, Any],
    outlines: Mapping[str, Any],
    min_share: float = MIN_SHARE,
) -> dict[str, Assignment | None]:
    """Assign each reference building to the footprint holding most of its outline.

    Args:
        footprints: Footprint polygons by footprint id, EPSG:2950.
        outlines: Reference roof outlines (2D) by ``gml:id``. ``Groupe*`` blocks are
            the caller's to leave out.
        min_share: Smallest share of the outline that makes an assignment.

    Returns:
        For every outline, its :class:`Assignment`, or ``None`` when no footprint
        holds ``min_share`` of it (``no-match``). An empty outline is never assigned.
    """
    ids = sorted(footprints)
    geoms = np.array([footprints[i] for i in ids], dtype=object)
    tree = shapely.STRtree(geoms)
    result: dict[str, Assignment | None] = {}
    for gml_id, outline in outlines.items():
        area = float(outline.area) if not outline.is_empty else 0.0
        best: Assignment | None = None
        if area > 0:
            # Candidates in id order, so that on a tie the lower id is kept.
            for k in sorted(tree.query(outline, predicate="intersects").tolist()):
                share = float(shapely.intersection(geoms[k], outline).area) / area
                if best is None or share > best.share + 1e-9:
                    best = Assignment(ids[k], min(share, 1.0))
        result[gml_id] = best if best is not None and best.share >= min_share else None
    return result
