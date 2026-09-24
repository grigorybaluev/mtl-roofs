"""Plane fitting must recover a known plane from noisy points, and real roofs' planes."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pytest
import shapely
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mtl_roofs.geometry.clip import clip_footprint
from mtl_roofs.geometry.planes import (
    Plane,
    PlaneDetection,
    PlaneParams,
    PlaneStatus,
    _merge,
    detect_planes,
    downslope_depth,
    fit_plane,
    largest_component,
    local_normals,
    ransac_planes,
)
from mtl_roofs.io.lidar import read_points
from tests.conftest import points_on_plane

FINITE = {"allow_nan": False, "allow_infinity": False}
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_fit_plane_recovers_exact_plane(rng: np.random.Generator) -> None:
    pts = points_on_plane((0.3, -0.4, 0.86), 12.5, 200, rng)
    plane = fit_plane(pts)
    expected = np.array([0.3, -0.4, 0.86])
    expected = expected / np.linalg.norm(expected)
    assert np.allclose(np.abs(plane.normal), np.abs(expected), atol=1e-9)
    assert plane.distance(pts).max() < 1e-9


def test_fit_plane_is_robust_to_noise(rng: np.random.Generator) -> None:
    truth = Plane((0.0, 0.0, 1.0), 30.0)
    pts = points_on_plane(truth.normal, truth.offset, 5000, rng, noise=0.05)
    fitted = fit_plane(pts)
    # 5 cm of noise on 5000 points should leave well under a degree of error.
    assert fitted.angle_to(truth) < 0.5
    assert abs(fitted.offset - truth.offset) < 0.01


def test_normal_is_canonically_upward(rng: np.random.Generator) -> None:
    pts = points_on_plane((0.0, 0.0, -1.0), -8.0, 100, rng)
    assert fit_plane(pts).normal[2] >= 0.0


@pytest.mark.parametrize(
    ("points", "match"),
    [
        (np.zeros((2, 3)), "at least 3"),
        (np.zeros((5, 2)), r"\(n, 3\)"),
        (np.stack([np.arange(5.0)] * 3, axis=1), "collinear"),
    ],
)
def test_fit_plane_rejects_bad_input(points: np.ndarray, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        fit_plane(points)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    nx=st.floats(-1, 1, **FINITE),
    ny=st.floats(-1, 1, **FINITE),
    offset=st.floats(-50, 50, **FINITE),
    noise=st.floats(0.0, 0.08, **FINITE),
)
def test_property_any_plane_is_recovered(nx: float, ny: float, offset: float, noise: float) -> None:
    """For any roof-like orientation, the fit recovers the plane within tolerance.

    ``nz`` is held positive so the normal is a roof, not a wall: a vertical plane is
    not something the roof pipeline is required to fit.
    """
    normal = np.array([nx, ny, 1.0])
    normal = normal / np.linalg.norm(normal)
    generator = np.random.default_rng(7)
    pts = points_on_plane(tuple(normal), offset, 400, generator, noise=noise)
    fitted = fit_plane(pts)
    assert fitted.angle_to(Plane(tuple(normal), offset)) < 2.0


def test_ransac_separates_two_roof_faces(rng: np.random.Generator) -> None:
    """A gable: two planes meeting at a ridge, plus outliers."""
    left = points_on_plane((-0.5, 0.0, 0.866), 20.0, 800, rng, noise=0.03)
    right = points_on_plane((0.5, 0.0, 0.866), 20.0, 800, rng, noise=0.03)
    outliers = rng.uniform(-30, 30, size=(120, 3))
    cloud = np.vstack([left, right, outliers])

    planes = ransac_planes(cloud, threshold=0.15, min_inliers=200, rng=rng)

    assert len(planes) == 2
    angles = sorted(p.slope_degrees for p, _ in planes)
    assert all(25.0 < a < 35.0 for a in angles)
    # The two faces are mirror images, so they must meet at twice the slope.
    assert planes[0][0].angle_to(planes[1][0]) == pytest.approx(60.0, abs=3.0)


def test_ransac_returns_nothing_for_pure_noise(rng: np.random.Generator) -> None:
    cloud = rng.uniform(-50, 50, size=(500, 3))
    assert ransac_planes(cloud, threshold=0.05, min_inliers=200, rng=rng) == []


# --- Building blocks --------------------------------------------------------------


def test_local_normals_of_a_plane_are_its_normal(rng: np.random.Generator) -> None:
    truth = np.array([0.3, 0.0, 0.954])
    pts = points_on_plane(tuple(truth), 10.0, 500, rng)
    normals = local_normals(pts, k=10)
    assert np.all(normals[:, 2] > 0)
    assert np.allclose(np.abs(normals @ (truth / np.linalg.norm(truth))), 1.0, atol=1e-6)


def test_local_normals_are_nan_when_too_few_points() -> None:
    assert np.isnan(local_normals(np.zeros((2, 3)))).all()


def test_largest_component_keeps_the_bigger_cluster() -> None:
    near = np.column_stack([np.arange(10) * 0.5, np.zeros(10), np.zeros(10)])
    far = np.column_stack([np.arange(4) * 0.5 + 100.0, np.zeros(4), np.zeros(4)])
    assert list(largest_component(np.vstack([far, near]), 0.75)) == list(range(4, 14))


def test_downslope_depth_measures_along_the_slope() -> None:
    plane = Plane((0.5, 0.0, 0.866), 0.0)
    strip = np.column_stack([np.linspace(0, 0.4, 50), np.linspace(0, 10, 50), np.zeros(50)])
    assert downslope_depth(plane, strip) == pytest.approx(0.36, abs=0.01)
    assert downslope_depth(Plane((0.0, 0.0, 1.0), 0.0), strip) == float("inf")


def test_merge_joins_a_split_face_but_not_a_step(rng: np.random.Generator) -> None:
    roof = points_on_plane((0.0, 0.0, 1.0), 20.0, 2000, rng, noise=0.03)
    halves = [np.flatnonzero(roof[:, 0] < 0), np.flatnonzero(roof[:, 0] >= 0)]
    split = [(fit_plane(roof[i]), i) for i in halves]
    assert len(_merge(roof, split, 0.15, 0.75, 5.0)) == 1

    stepped = roof.copy()
    stepped[halves[1], 2] += 0.5
    step = [(fit_plane(stepped[i]), i) for i in halves]
    assert len(_merge(stepped, step, 0.15, 0.75, 5.0)) == 2


# --- Synthetic buildings with the clutter real roofs have -------------------------


def _grid(x0: float, x1: float, y0: float, y1: float, spacing: float) -> np.ndarray:
    xs, ys = np.meshgrid(np.arange(x0, x1, spacing), np.arange(y0, y1, spacing))
    return np.column_stack([xs.ravel(), ys.ravel()])


def _walls(
    width: float, depth: float, z0: float, z1: float, rng: np.random.Generator
) -> np.ndarray:
    """Facade points just inside a ``width`` x ``depth`` footprint, as the clipper keeps them."""
    n = 400
    z = rng.uniform(z0, z1, n)
    t = rng.uniform(0, 1, n)
    side = rng.integers(0, 4, n)
    x = np.select([side == 0, side == 1, side == 2], [t * width, t * width, 0.05], width - 0.05)
    y = np.select([side == 0, side == 1, side == 2], [0.05, depth - 0.05, t * depth], t * depth)
    return np.column_stack([x, y, z])


def synthetic_gable(rng: np.random.Generator, slope_deg: float = 35.0) -> np.ndarray:
    """A 10 x 8 m gable, ridge along y, at ~18 pts/m², with walls below the eaves."""
    xy = _grid(0.0, 10.0, 0.0, 8.0, 0.24) + rng.normal(0, 0.02, (1, 2))
    z = 60.0 - np.tan(np.radians(slope_deg)) * np.abs(xy[:, 0] - 5.0)
    roof = np.column_stack([xy, z + rng.normal(0, 0.05, len(z))])
    eave = 60.0 - np.tan(np.radians(slope_deg)) * 5.0
    return np.vstack([roof, _walls(10.0, 8.0, eave - 6.0, eave, rng)])


def synthetic_flat(rng: np.random.Generator) -> np.ndarray:
    """A 12 x 10 m flat roof with walls and a 0.5 m parapet, at ~18 pts/m².

    The parapet is one point wide, as on the fixtures: a cap wide enough to hold
    several rows of points is a real horizontal surface and would rightly be a plane.
    """
    xy = _grid(0.0, 12.0, 0.0, 10.0, 0.24)
    edge = np.minimum.reduce([xy[:, 0], 12.0 - xy[:, 0], xy[:, 1], 10.0 - xy[:, 1]])
    z = np.where(edge < 0.2, 50.5, 50.0) + rng.normal(0, 0.05, len(xy))
    return np.vstack([np.column_stack([xy, z]), _walls(12.0, 10.0, 44.0, 50.0, rng)])


def test_a_clean_gable_yields_exactly_two_planes_at_the_ridge_angle(
    rng: np.random.Generator,
) -> None:
    result = detect_planes(synthetic_gable(rng, slope_deg=35.0))
    assert result.status is PlaneStatus.OK
    assert len(result.planes) == 2
    (a, _), (b, _) = result.planes
    assert a.slope_degrees == pytest.approx(35.0, abs=1.0)
    assert b.slope_degrees == pytest.approx(35.0, abs=1.0)
    # Two faces of slope s meet at a ridge whose normals are 2s apart.
    assert a.angle_to(b) == pytest.approx(70.0, abs=2.0)


def test_a_flat_roof_with_parapet_and_walls_yields_one_plane(rng: np.random.Generator) -> None:
    result = detect_planes(synthetic_flat(rng))
    assert len(result.planes) == 1
    assert result.planes[0][0].slope_degrees < 1.0


# --- Status and reproducibility ----------------------------------------------------


def test_nothing_planar_is_no_planes_not_an_empty_ok(rng: np.random.Generator) -> None:
    result = detect_planes(rng.uniform(-50, 50, size=(500, 3)))
    assert result.status is PlaneStatus.NO_PLANES
    assert result.planes == []
    assert result.coverage == 0.0


def test_no_points_is_no_planes() -> None:
    result = detect_planes(np.empty((0, 3)))
    assert result.status is PlaneStatus.NO_PLANES
    assert result.n_points == 0


def test_a_seeded_run_is_reproducible_and_logs_its_seed(
    rng: np.random.Generator, caplog: pytest.LogCaptureFixture
) -> None:
    cloud = synthetic_gable(rng)
    params = PlaneParams(seed=1234)
    with caplog.at_level(logging.INFO, logger="mtl_roofs.geometry.planes"):
        first = detect_planes(cloud, params)
    second = detect_planes(cloud, params)
    assert first.seed == 1234
    assert "seed=1234" in caplog.text
    assert [p for p, _ in first.planes] == [p for p, _ in second.planes]
    for (_, i), (_, j) in zip(first.planes, second.planes, strict=True):
        assert np.array_equal(i, j)


# --- The fixtures: real roofs ------------------------------------------------------


def _footprint(fid: str) -> shapely.Polygon:
    data = json.loads((FIXTURES / "footprints" / f"{fid}.geojson").read_text())
    return shapely.from_geojson(json.dumps(data["features"][0]["geometry"]))


@pytest.fixture(scope="module")
def detected() -> dict[str, PlaneDetection]:
    index = json.loads((FIXTURES / "index.json").read_text())
    results = {}
    for footprint in index["footprints"]:
        fid = footprint["footprint_id"]
        clipped = clip_footprint(read_points(FIXTURES / "points" / f"{fid}.laz"), _footprint(fid))
        results[fid] = detect_planes(clipped.roof.xyz)
    return results


def test_every_fixture_yields_disjoint_roof_planes(detected: dict[str, PlaneDetection]) -> None:
    assert len(detected) == 20
    max_slope = PlaneParams().max_slope_deg
    for fid, result in detected.items():
        assert result.status is PlaneStatus.OK, fid
        indices = np.concatenate([idx for _, idx in result.planes])
        assert len(indices) == len(np.unique(indices)), fid
        assert all(p.slope_degrees <= max_slope for p, _ in result.planes), fid


def test_the_single_face_flat_pair_is_one_roof_and_one_lower_section(
    detected: dict[str, PlaneDetection],
) -> None:
    """``fp-2929000-50353029``: two buildings, each a single flat face in the reference.

    The LiDAR agrees on the main roof, and it comes out as one plane, not fragments.
    It also shows an 18 m² section on the south side, 6° and 0.3-0.6 m lower, planar
    to 2.6 cm RMS. The reference generalises it away. It is kept as its own plane:
    merging it would hide geometry the data measures.
    """
    (roof, roof_idx), (section, section_idx) = detected["fp-2929000-50353029"].planes
    assert len(detected["fp-2929000-50353029"].planes) == 2
    assert roof.slope_degrees < 1.0
    assert len(roof_idx) > 8 * len(section_idx)
    assert 4.0 < section.slope_degrees < 8.0
    points = _roof_points("fp-2929000-50353029")
    below = np.median(points[roof_idx, 2]) - np.median(points[section_idx, 2])
    assert below > 2 * PlaneParams().threshold


def _roof_points(fid: str) -> np.ndarray:
    points = read_points(FIXTURES / "points" / f"{fid}.laz")
    xyz: np.ndarray = clip_footprint(points, _footprint(fid)).roof.xyz
    return xyz
