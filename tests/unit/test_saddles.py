"""Saddle vertices: faces alternating in height around a vertex (#84)."""

from __future__ import annotations

import numpy as np
import pytest
import shapely

from mtl_roofs.geometry.extrude import ExtrusionStatus, extrude_lod2, is_watertight
from mtl_roofs.geometry.planes import Plane
from mtl_roofs.geometry.roof import RoofFace, RoofParams, _crossings, _resolve_saddles

X0, Y0 = 292_500.0, 5_035_500.0
GROUND = 10.0


def _flat(z: float) -> Plane:
    return Plane((0.0, 0.0, 1.0), z)


def _quadrants(heights: tuple[float, float, float, float]) -> list[RoofFace]:
    """Four 5 x 5 m faces meeting at (5, 5), heights counter-clockwise from south-west."""
    boxes = [
        shapely.box(X0, Y0, X0 + 5, Y0 + 5),
        shapely.box(X0 + 5, Y0, X0 + 10, Y0 + 5),
        shapely.box(X0 + 5, Y0 + 5, X0 + 10, Y0 + 10),
        shapely.box(X0, Y0 + 5, X0 + 5, Y0 + 10),
    ]
    # Node the shared edges at their midpoints, as a real coverage is.
    return [
        RoofFace(i, _flat(z), shapely.Polygon(shapely.segmentize(b, 5.0).exterior))
        for i, (b, z) in enumerate(zip(boxes, heights, strict=True))
    ]


FOOTPRINT = shapely.box(X0, Y0, X0 + 10, Y0 + 10)


@pytest.mark.parametrize(
    ("heights", "expected"),
    [
        ([16.0, 14.0, 16.0, 14.0], 4),
        ([16.0, 15.0, 14.0, 13.0], 2),
        ([-np.inf, 16.0, 14.0, 16.0], 4),
        ([16.0, 16.0 + 1e-6, 14.0], 2),
    ],
)
def test_crossings(heights: list[float], expected: int) -> None:
    assert _crossings(heights, 1e-4) == expected


def test_a_checkerboard_vertex_is_not_watertight_unless_split() -> None:
    faces = _quadrants((16.0, 14.0, 16.0, 14.0))
    assert extrude_lod2(faces, GROUND).status is ExtrusionStatus.NOT_WATERTIGHT

    split = _resolve_saddles(FOOTPRINT, faces, RoofParams())
    extrusion = extrude_lod2(split, GROUND)
    assert extrusion.status is ExtrusionStatus.OK, extrusion.problems
    assert is_watertight(extrusion.solid.rings())
    # The partition still tiles the footprint, and a face changes by at most a thin
    # sliver along each of its two edges at the vertex: 5 cm by 5 m between them.
    assert sum(f.polygon.area for f in split) == pytest.approx(100.0)
    for before, after in zip(faces, split, strict=True):
        assert after.polygon.symmetric_difference(before.polygon).area < 0.05 * 5.0
    assert extrusion.solid.volume() == pytest.approx(25 * (6 + 4 + 6 + 4), abs=0.5)


def test_a_vertex_whose_heights_do_not_alternate_is_left_alone() -> None:
    faces = _quadrants((16.0, 15.0, 14.0, 13.0))
    assert _resolve_saddles(FOOTPRINT, faces, RoofParams()) is faces
    assert extrude_lod2(faces, GROUND).status is ExtrusionStatus.OK


def test_a_saddle_on_the_outline_is_split_without_moving_the_outline() -> None:
    # A low wedge reaching the south edge at (5, 0) between two high triangles: round
    # that vertex the heights go outside (ground), east 16, wedge 12, west 16, which
    # crosses 14 four times.
    def poly(*xy: tuple[float, float]) -> shapely.Polygon:
        return shapely.Polygon([(X0 + x, Y0 + y) for x, y in xy])

    faces = [
        RoofFace(0, _flat(16.0), poly((0, 0), (5, 0), (0, 5))),
        RoofFace(1, _flat(16.0), poly((5, 0), (10, 0), (10, 5))),
        RoofFace(2, _flat(12.0), poly((5, 0), (10, 5), (10, 10), (0, 10), (0, 5))),
    ]
    assert extrude_lod2(faces, GROUND).status is ExtrusionStatus.NOT_WATERTIGHT

    split = _resolve_saddles(FOOTPRINT, faces, RoofParams())
    extrusion = extrude_lod2(split, GROUND)
    assert extrusion.status is ExtrusionStatus.OK, extrusion.problems
    outline = shapely.union_all([f.polygon for f in split])
    assert outline.symmetric_difference(FOOTPRINT).area < 1e-9
