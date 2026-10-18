"""Roof partition and extrusion to a watertight LOD2 solid (#20)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
import shapely

from mtl_roofs.geometry.extrude import (
    ExtrusionStatus,
    Solid,
    SurfaceKind,
    extrude_lod2,
    is_consistently_oriented,
    is_watertight,
)
from mtl_roofs.geometry.planes import Plane, fit_plane
from mtl_roofs.geometry.roof import RoofFace, partition_roof, plane_height
from mtl_roofs.io.lidar import read_points
from mtl_roofs.io.reference import newell_normal
from mtl_roofs.pipeline import Status, reconstruct_footprint

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
X0, Y0 = 292_500.0, 5_035_500.0
GROUND = 10.0
K = np.tan(np.radians(35.0))

Surface = Callable[[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]


def _sample(
    surface: Surface, footprint: shapely.Polygon, spacing: float = 0.25
) -> tuple[np.ndarray, list[tuple[Plane, np.ndarray]]]:
    """Exact points of ``surface(x, y) -> (z, label)`` over a footprint, local x, y."""
    xmin, ymin, xmax, ymax = footprint.bounds
    gx, gy = np.meshgrid(
        np.arange(xmin + spacing / 2, xmax, spacing), np.arange(ymin + spacing / 2, ymax, spacing)
    )
    gx, gy = gx.ravel(), gy.ravel()
    keep = shapely.contains_xy(footprint, gx, gy)
    gx, gy = gx[keep], gy[keep]
    z, label = surface(gx - X0, gy - Y0)
    pts = np.column_stack([gx, gy, z])
    planes = [
        (fit_plane(pts[label == face]), np.flatnonzero(label == face)) for face in np.unique(label)
    ]
    return pts, planes


def _box(width: float, depth: float) -> shapely.Polygon:
    return shapely.box(X0, Y0, X0 + width, Y0 + depth)


def _gable(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Ridge along x at y = 4, 35° both sides, ridge at 20 m."""
    return 20.0 - K * np.abs(y - 4.0), (y > 4.0).astype(int)


def _hip(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """12 x 8 m, 35° all round, ridge from x = 4 to 8 at 20 m."""
    faces = np.stack([20 - K * (4 - y), 20 - K * (y - 4), 20 - K * (4 - x), 20 - K * (x - 8)])
    return faces.min(axis=0), faces.argmin(axis=0)


def _steps(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two flat roofs, 15 m west of x = 5 and 18 m east of it."""
    east = x > 5.0
    return np.where(east, 18.0, 15.0), east.astype(int)


def _volume(surface: Surface, footprint: shapely.Polygon, spacing: float = 0.02) -> float:
    """Volume under a roof surface by midpoint quadrature, for an independent check."""
    xmin, ymin, xmax, ymax = footprint.bounds
    gx, gy = np.meshgrid(
        np.arange(xmin + spacing / 2, xmax, spacing), np.arange(ymin + spacing / 2, ymax, spacing)
    )
    inside = shapely.contains_xy(footprint, gx, gy)
    z, _ = surface(gx[inside] - X0, gy[inside] - Y0)
    return float(np.sum(z - GROUND) * spacing**2)


def _assert_valid(solid: Solid) -> None:
    rings = solid.rings()
    assert is_watertight(rings)
    assert is_consistently_oriented(rings)
    assert solid.volume() > 0
    for surface in solid.surfaces:
        normal = newell_normal(
            [tuple(solid.vertices[v]) for v in [*surface.rings[0], surface.rings[0][0]]]
        )
        if surface.kind is SurfaceKind.ROOF:
            assert normal[2] > 0
        elif surface.kind is SurfaceKind.GROUND:
            assert normal[2] == pytest.approx(-1.0)
        else:
            assert normal[2] == pytest.approx(0.0, abs=1e-9)


def _interior_walls(solid: Solid) -> list[int]:
    """Walls that do not reach the ground: steps between roof faces."""
    return [
        i
        for i, s in enumerate(solid.surfaces)
        if s.kind is SurfaceKind.WALL and solid.vertices[s.rings[0], 2].min() > GROUND + 1e-9
    ]


# --- exact synthetic roofs -------------------------------------------------------


@pytest.mark.parametrize(
    ("surface", "width", "depth"), [(_gable, 10.0, 8.0), (_hip, 12.0, 8.0)], ids=["gable", "hip"]
)
def test_an_exact_pitched_roof_is_continuous_and_has_the_right_volume(
    surface: Surface, width: float, depth: float
) -> None:
    footprint = _box(width, depth)
    pts, planes = _sample(surface, footprint)
    faces = partition_roof(footprint, pts, planes)
    assert len(faces) == len(planes)
    extrusion = extrude_lod2(faces, GROUND)
    assert extrusion.status is ExtrusionStatus.OK, extrusion.problems
    _assert_valid(extrusion.solid)
    # Boundaries snapped onto the plane intersections: no step anywhere.
    assert _interior_walls(extrusion.solid) == []
    assert extrusion.solid.volume() == pytest.approx(_volume(surface, footprint), rel=1e-4)


def test_a_step_between_flat_roofs_gets_one_vertical_wall() -> None:
    footprint = _box(10.0, 6.0)
    pts, planes = _sample(_steps, footprint)
    extrusion = extrude_lod2(partition_roof(footprint, pts, planes), GROUND)
    assert extrusion.status is ExtrusionStatus.OK
    _assert_valid(extrusion.solid)
    (wall,) = _interior_walls(extrusion.solid)
    z = extrusion.solid.vertices[extrusion.solid.surfaces[wall].rings[0], 2]
    assert sorted(set(np.round(z, 6))) == [15.0, 18.0]
    assert extrusion.solid.volume() == pytest.approx(60 * 6.5, rel=1e-3)


def test_a_courtyard_is_a_hole_in_the_roof_and_the_floor() -> None:
    footprint = shapely.Polygon(
        shapely.box(X0, Y0, X0 + 12, Y0 + 12).exterior.coords,
        [shapely.box(X0 + 4, Y0 + 4, X0 + 8, Y0 + 8).exterior.coords],
    )
    pts, planes = _sample(lambda x, y: (np.full_like(x, 16.0), np.zeros(len(x), int)), footprint)
    extrusion = extrude_lod2(partition_roof(footprint, pts, planes), GROUND)
    assert extrusion.status is ExtrusionStatus.OK
    _assert_valid(extrusion.solid)
    (ground,) = [s for s in extrusion.solid.surfaces if s.kind is SurfaceKind.GROUND]
    assert len(ground.rings) == 2
    assert extrusion.solid.volume() == pytest.approx(128 * 6.0)


# --- hand-built partitions, for the cases snapping would otherwise hide -------------


def _faces(*parts: tuple[shapely.Polygon, Plane]) -> list[RoofFace]:
    return [RoofFace(i, plane, poly) for i, (poly, plane) in enumerate(parts)]


def _tilted(z0: float, gx: float, gy: float) -> Plane:
    """The plane ``z = z0 + gx (x - X0) + gy (y - Y0)``."""
    n = np.array([-gx, -gy, 1.0]) / np.sqrt(gx**2 + gy**2 + 1.0)
    return Plane((float(n[0]), float(n[1]), float(n[2])), float(n @ [X0, Y0, z0]))


def test_faces_that_swap_height_along_their_edge_are_split_at_the_crossing() -> None:
    # Two faces either side of x = 5: the west one is flat at 15 m, the east one rises
    # northwards from 14 to 16 m, so it is lower at the south end and higher at the north.
    west = shapely.box(X0, Y0, X0 + 5, Y0 + 6)
    east = shapely.box(X0 + 5, Y0, X0 + 10, Y0 + 6)
    faces = _faces((west, _tilted(15.0, 0.0, 0.0)), (east, _tilted(14.0, 0.0, 2 / 6)))
    extrusion = extrude_lod2(faces, GROUND)
    assert extrusion.status is ExtrusionStatus.OK, extrusion.problems
    _assert_valid(extrusion.solid)
    assert len(_interior_walls(extrusion.solid)) == 2
    assert extrusion.solid.volume() == pytest.approx(30 * 5 + 30 * 5)


def test_three_faces_at_three_heights_meet_without_a_t_junction() -> None:
    # Three flat faces meet at (5, 3): the vertical edge there is split by the middle
    # height, or the walls would not share it.
    west = shapely.Polygon(
        [(X0, Y0), (X0 + 5, Y0), (X0 + 5, Y0 + 3), (X0 + 5, Y0 + 6), (X0, Y0 + 6)]
    )
    south = shapely.box(X0 + 5, Y0, X0 + 10, Y0 + 3)
    north = shapely.box(X0 + 5, Y0 + 3, X0 + 10, Y0 + 6)
    flat = [_tilted(z, 0.0, 0.0) for z in (14.0, 16.0, 18.0)]
    faces = _faces((west, flat[0]), (south, flat[1]), (north, flat[2]))
    extrusion = extrude_lod2(faces, GROUND)
    assert extrusion.status is ExtrusionStatus.OK, extrusion.problems
    _assert_valid(extrusion.solid)
    assert extrusion.solid.volume() == pytest.approx(30 * 4 + 15 * 6 + 15 * 8)


def test_a_roof_reaching_the_ground_is_not_watertight() -> None:
    faces = _faces((_box(10.0, 6.0), _tilted(GROUND + 0.2, 0.0, 0.0)))
    extrusion = extrude_lod2(faces, GROUND)
    assert extrusion.status is ExtrusionStatus.NOT_WATERTIGHT
    assert "above the ground" in extrusion.problems[0]


def test_no_faces_is_an_error() -> None:
    with pytest.raises(ValueError, match="no faces"):
        extrude_lod2([], GROUND)


def test_plane_height_inverts_the_plane() -> None:
    plane = _tilted(15.0, 0.1, -0.2)
    z = plane_height(plane, np.array([X0 + 3.0]), np.array([Y0 + 2.0]))
    assert z[0] == pytest.approx(15.0 + 0.3 - 0.4)


# --- the fixtures ------------------------------------------------------------------


def _footprint(fid: str) -> shapely.Polygon:
    data = json.loads((FIXTURES / "footprints" / f"{fid}.geojson").read_text())
    return shapely.from_geojson(json.dumps(data["features"][0]["geometry"]))


FIXTURE_IDS = [
    f["footprint_id"] for f in json.loads((FIXTURES / "index.json").read_text())["footprints"]
]


@pytest.mark.parametrize("fid", FIXTURE_IDS)
def test_every_fixture_reconstructs_to_a_closed_solid(fid: str) -> None:
    footprint = _footprint(fid)
    result = reconstruct_footprint(read_points(FIXTURES / "points" / f"{fid}.laz"), footprint)
    assert result.status is Status.OK, result.reason
    assert result.extrusion is not None
    _assert_valid(result.extrusion.solid)

    polygons = np.array([f.polygon for f in result.faces], dtype=object)
    assert bool(shapely.coverage_is_valid(polygons))
    assert sum(p.area for p in polygons) == pytest.approx(footprint.area, rel=1e-9)
    # The floor is the footprint.
    (ground,) = [s for s in result.extrusion.solid.surfaces if s.kind is SurfaceKind.GROUND]
    floor = shapely.Polygon(result.extrusion.solid.vertices[ground.rings[0], :2])
    assert floor.area == pytest.approx(shapely.Polygon(footprint.exterior).area, rel=1e-9)


def test_a_partition_that_is_not_noded_is_reported_not_watertight() -> None:
    # The west face's east edge has no vertex where the other two faces meet it: a
    # T-junction. It must be reported, not loop or pass.
    west = shapely.box(X0, Y0, X0 + 5, Y0 + 6)
    south = shapely.box(X0 + 5, Y0, X0 + 10, Y0 + 3)
    north = shapely.box(X0 + 5, Y0 + 3, X0 + 10, Y0 + 6)
    flat = _tilted(16.0, 0.0, 0.0)
    extrusion = extrude_lod2(_faces((west, flat), (south, flat), (north, flat)), GROUND)
    assert extrusion.status is ExtrusionStatus.NOT_WATERTIGHT
