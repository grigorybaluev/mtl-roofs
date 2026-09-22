"""Matching reconstructions to reference buildings.

The footprint layer carries no building identifier and the CityGML model uses its
own ``gml:id``, so there is no join key between the two. Matching is therefore
spatial, and its failure rate is a reported metric rather than a hidden detail.
See ``docs/adr/0003-building-matching.md``.
"""

from __future__ import annotations


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


def match_buildings(*_args: object, **_kwargs: object) -> object:
    """Match reconstructions to reference buildings by maximum IoU.

    Not implemented yet: tracked by the "Building matching" issue.
    """
    raise NotImplementedError
