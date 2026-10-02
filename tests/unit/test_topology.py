"""The topology solver (docs/topology-solver.md, ADR 0006)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
import shapely

from mtl_roofs.geometry.clip import clip_footprint
from mtl_roofs.geometry.planes import Plane, detect_planes, fit_plane
from mtl_roofs.geometry.topology import (
    FailureReason,
    RelationKind,
    TopologyParams,
    TopologyStatus,
    WarningKind,
    _build,
    _face,
    solve_topology,
)
from mtl_roofs.io.lidar import read_points

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
#: Realistic EPSG:2950 coordinates, so the local origin is exercised.
X0, Y0 = 292_500.0, 5_035_500.0
SLOPE = np.radians(35.0)

Surface = Callable[[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]


def _sample(
    width: float,
    depth: float,
    surface: Surface,
    rng: np.random.Generator | None = None,
    noise: float = 0.0,
    spacing: float = 0.25,
) -> tuple[np.ndarray, list[tuple[Plane, np.ndarray]], shapely.Polygon]:
    """Grid-sample a roof ``surface(x, y) -> (z, face label)`` over a rectangle."""
    xs = np.arange(spacing / 2, width, spacing)
    ys = np.arange(spacing / 2, depth, spacing)
    gx, gy = (a.ravel() for a in np.meshgrid(xs, ys))
    z, label = surface(gx, gy)
    if rng is not None and noise:
        z = z + rng.normal(0.0, noise, len(z))
    pts = np.column_stack([gx + X0, gy + Y0, z])
    planes = []
    for face in np.unique(label):
        idx = np.flatnonzero(label == face)
        planes.append((fit_plane(pts[idx]), idx))
    return pts, planes, shapely.box(X0, Y0, X0 + width, Y0 + depth)


def gable(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """10 x 8 m, ridge along x at y = 4, 35° both sides, ridge at 20 m."""
    k = np.tan(SLOPE)
    return 20.0 - k * np.abs(y - 4.0), (y > 4.0).astype(int)


def hip(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """12 x 8 m hip roof, 35° all round, ridge from x = 4 to 8 at 20 m."""
    k = np.tan(SLOPE)
    faces = np.stack([20 - k * (4 - y), 20 - k * (y - 4), 20 - k * (4 - x), 20 - k * (x - 8)])
    return faces.min(axis=0), faces.argmin(axis=0)


def pyramid(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """8 x 8 m pyramid, 35° all round, apex at (4, 4, 20)."""
    k = np.tan(SLOPE)
    faces = np.stack([20 - k * (4 - y), 20 - k * (y - 4), 20 - k * (4 - x), 20 - k * (x - 4)])
    return faces.min(axis=0), faces.argmin(axis=0)


def _truth(surface: Surface, width: float, depth: float) -> list[Plane]:
    _, planes, _ = _sample(width, depth, surface)
    return [p for p, _ in planes]


def _azimuth_off_axis(plane: Plane) -> float:
    """Degrees between a plane's downslope direction and the nearest x/y axis."""
    a = np.degrees(np.arctan2(plane.normal[1], plane.normal[0])) % 90.0
    return float(min(a, 90.0 - a))


def _skew(pts: np.ndarray, idx: np.ndarray, degrees: float) -> np.ndarray:
    """Rotate one face's points about the vertical through its centroid."""
    out = pts.copy()
    c = pts[idx].mean(axis=0)
    t = np.radians(degrees)
    rot = np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]])
    out[idx, :2] = (pts[idx, :2] - c[:2]) @ rot.T + c[:2]
    return out


# --- exact geometry is a fixed point ---------------------------------------------


@pytest.mark.parametrize(
    ("surface", "width", "depth"), [(gable, 10.0, 8.0), (hip, 12.0, 8.0), (pyramid, 8.0, 8.0)]
)
def test_exact_roofs_are_returned_exactly(surface: Surface, width: float, depth: float) -> None:
    pts, planes, footprint = _sample(width, depth, surface)
    result = solve_topology(pts, planes, footprint)
    assert result.status is TopologyStatus.OK
    for (got, _), (want, _) in zip(result.planes, planes, strict=True):
        assert np.allclose(got.normal, want.normal, atol=1e-9)
        assert got.offset == pytest.approx(want.offset, abs=1e-6)
    assert max(result.rotation_deg) < 1e-6


# --- noisy and skewed roofs converge to the known geometry -------------------------


def test_noisy_skewed_gable_converges_to_the_known_gable(rng: np.random.Generator) -> None:
    pts, planes, footprint = _sample(10.0, 8.0, gable, rng, noise=0.03)
    north = planes[1][1]
    pts = _skew(pts, north, 2.0)
    planes = [(fit_plane(pts[idx]), idx) for _, idx in planes]
    assert _azimuth_off_axis(planes[1][0]) > 1.5

    result = solve_topology(pts, planes, footprint)

    assert result.status is TopologyStatus.OK
    (south, _), (north_plane, _) = result.planes
    for plane in (south, north_plane):
        assert _azimuth_off_axis(plane) < 0.05
        assert plane.slope_degrees == pytest.approx(35.0, abs=0.3)
    assert abs(south.slope_degrees - north_plane.slope_degrees) < 0.05
    assert [r.kind for r in result.relations] == [RelationKind.RIDGE]
    ridge = np.cross(south.normal, north_plane.normal)
    assert abs(ridge[2]) / np.linalg.norm(ridge) < np.sin(np.radians(0.05))
    assert not [w for w in result.warnings if w.kind is WarningKind.PRIOR_CONFLICT]


def test_noisy_pyramid_meets_at_one_apex(rng: np.random.Generator) -> None:
    pts, planes, footprint = _sample(8.0, 8.0, pyramid, rng, noise=0.03)
    result = solve_topology(pts, planes, footprint)

    assert result.status is TopologyStatus.OK
    (corner,) = result.corners
    assert corner.planes == (0, 1, 2, 3)
    apex = np.asarray(corner.point)
    assert np.allclose(apex, [X0 + 4.0, Y0 + 4.0, 20.0], atol=0.05)
    for plane, _ in result.planes:
        assert abs(float(np.dot(plane.normal, apex)) - plane.offset) < 1e-3
        assert plane.slope_degrees == pytest.approx(35.0, abs=0.3)
        assert _azimuth_off_axis(plane) < 0.05


def test_noisy_hip_converges_to_the_known_hip(rng: np.random.Generator) -> None:
    pts, planes, footprint = _sample(12.0, 8.0, hip, rng, noise=0.03)
    result = solve_topology(pts, planes, footprint)
    truth = _truth(hip, 12.0, 8.0)

    assert result.status is TopologyStatus.OK
    assert result.corners == []  # two three-face corners: exactly determined
    for (got, _), want in zip(result.planes, truth, strict=True):
        assert got.angle_to(want) < 0.3
        assert _azimuth_off_axis(got) < 0.05
    kinds = sorted(r.kind.value for r in result.relations)
    # One ridge (the long faces); the end triangles meet the long faces along hips
    # and each other only at the ridge ends, so they are not adjacent.
    assert kinds == ["break", "break", "break", "break", "ridge"]


# --- gates -------------------------------------------------------------------------


def test_a_face_outside_every_gate_keeps_its_orientation(rng: np.random.Generator) -> None:
    pts, planes, footprint = _sample(10.0, 8.0, gable, rng, noise=0.03)
    pts = _skew(pts, planes[1][1], 20.0)
    planes = [(fit_plane(pts[idx]), idx) for _, idx in planes]
    result = solve_topology(pts, planes, footprint)
    assert result.planes[1][0].angle_to(planes[1][0]) < 0.05


def test_a_near_level_face_is_levelled_and_the_level_prior_can_be_turned_off(
    rng: np.random.Generator,
) -> None:
    def drained(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return 15.0 - np.tan(np.radians(1.0)) * x, np.zeros_like(x, dtype=int)

    pts, planes, footprint = _sample(10.0, 8.0, drained, rng, noise=0.02)
    levelled = solve_topology(pts, planes, footprint)
    assert levelled.planes[0][0].slope_degrees < 0.05
    kept = solve_topology(pts, planes, footprint, TopologyParams(level_gate_deg=0.0))
    assert kept.planes[0][0].slope_degrees == pytest.approx(1.0, abs=0.1)


def test_near_parallel_neighbours_are_a_step_not_an_intersection() -> None:
    def stepped(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        low = x < 5.0
        return np.where(low, 12.0, 15.0 + 0.02 * x), (~low).astype(int)

    pts, planes, footprint = _sample(10.0, 8.0, stepped)
    result = solve_topology(pts, planes, footprint)
    assert [r.kind for r in result.relations] == [RelationKind.STEP]
    assert result.corners == []


# --- failure reasons and warnings ---------------------------------------------------


def test_running_out_of_iterations_is_no_convergence_with_the_residual(
    rng: np.random.Generator,
) -> None:
    pts, planes, footprint = _sample(8.0, 8.0, pyramid, rng, noise=0.03)
    result = solve_topology(pts, planes, footprint, TopologyParams(max_iterations=1))
    assert result.status is TopologyStatus.SOLVER_FAILED
    assert result.reason is FailureReason.NO_CONVERGENCE
    assert result.iterations == 1
    assert result.final_cost > 0
    assert result.gradient_norm > TopologyParams().gtol


def test_an_undetermined_parameter_is_ill_conditioned() -> None:
    # A face one point wide: its tilt about its long axis is set by nothing.
    line = np.column_stack([np.linspace(0, 10, 50) + X0, np.full(50, Y0 + 4.0), np.full(50, 10.0)])
    pts = np.vstack([line, line + np.array([0.0, 1e-7, 0.0])])
    plane = Plane((0.0, 0.0, 1.0), 10.0)
    footprint = shapely.box(X0, Y0, X0 + 10, Y0 + 8)
    params = TopologyParams(sigma_angle_deg=1e6, level_gate_deg=0.0)
    result = solve_topology(pts, [(plane, np.arange(len(pts)))], footprint, params)
    assert result.status is TopologyStatus.SOLVER_FAILED
    assert result.reason is FailureReason.ILL_CONDITIONED
    assert result.rcond < params.rcond_min


def test_a_narrow_face_is_a_sliver() -> None:
    pts, planes, footprint = _sample(10.0, 0.3, gable, spacing=0.1)
    result = solve_topology(pts, planes, footprint)
    assert any(w.kind is WarningKind.SLIVER_FACE for w in result.warnings)


def test_a_corner_with_an_ill_conditioned_triple_is_skipped_with_a_warning() -> None:
    # A pyramid whose south face is split into a 35° and a 50° part. All five faces
    # meet at the apex, but the two south parts and the north face have normals in
    # one vertical plane, so that triple has no intersection point.
    def split_pyramid(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        z, label = pyramid(x, y)
        steep = (label == 0) & (x >= 4.0)
        z = np.where(steep, 20 - np.tan(np.radians(50.0)) * (4 - y), z)
        return z, np.where(steep, 4, label)

    pts, planes, footprint = _sample(8.0, 8.0, split_pyramid)
    result = solve_topology(pts, planes, footprint)
    # The apex is still constrained through the four faces that condition it.
    (corner,) = result.corners
    assert corner.planes == (1, 2, 3, 4)
    assert np.allclose(corner.point, [X0 + 4.0, Y0 + 4.0, 20.0], atol=1e-6)
    skipped = [w for w in result.warnings if w.kind is WarningKind.ILL_CONDITIONED_CORNER]
    assert skipped
    assert all({0, 1, 4} <= set(w.planes) for w in skipped)


def test_faces_meeting_along_parallel_lines_are_not_a_warning() -> None:
    # A mansard: three faces sloping the same way at 20°, 35° and 50°. Their normals
    # lie in one vertical plane, which is ordinary geometry, not a lost corner.
    def mansard(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        k = np.tan(np.radians([20.0, 35.0, 50.0]))
        z = np.stack([20 - k[0] * y, 20.5 - k[1] * y, 21.0 - k[2] * y])
        label = np.select([y < 2.0, y < 4.0], [0, 1], 2)
        return z[label, np.arange(len(y))], label

    pts, planes, footprint = _sample(10.0, 6.0, mansard)
    result = solve_topology(pts, planes, footprint, TopologyParams(adjacency_radius_m=2.5))
    assert result.warnings == []


def test_priors_overriding_the_evidence_is_a_prior_conflict(rng: np.random.Generator) -> None:
    pts, planes, footprint = _sample(10.0, 8.0, gable, rng, noise=0.01)
    pts = _skew(pts, planes[1][1], 4.0)
    planes = [(fit_plane(pts[idx]), idx) for _, idx in planes]
    params = TopologyParams(sigma_angle_deg=0.2)
    result = solve_topology(pts, planes, footprint, params)
    assert any(w.kind is WarningKind.PRIOR_CONFLICT and w.planes == (1,) for w in result.warnings)


# --- the Jacobian ------------------------------------------------------------------


def test_analytic_jacobian_matches_finite_differences(rng: np.random.Generator) -> None:
    pts, planes, footprint = _sample(8.0, 8.0, pyramid, rng, noise=0.05)
    pts = _skew(pts, planes[0][1], 1.5)
    origin = pts.mean(axis=0)
    p = TopologyParams()
    faces = [_face(pts[idx] - origin, p) for _, idx in planes]
    problem, _, _ = _build(faces, footprint, origin, p)
    assert problem.corners
    assert problem.aligned
    assert problem.ridges
    assert problem.symmetric
    x = rng.normal(0.0, 0.01, problem.n_params)
    _, jac = problem.residuals(x)
    h = 1e-7
    numeric = np.column_stack(
        [
            (problem.residuals(x + h * e)[0] - problem.residuals(x - h * e)[0]) / (2 * h)
            for e in np.eye(problem.n_params)
        ]
    )
    assert np.allclose(jac, numeric, rtol=1e-5, atol=1e-4 * np.abs(numeric).max())


# --- the fixtures ------------------------------------------------------------------


def _footprint(fid: str) -> shapely.Polygon:
    data = json.loads((FIXTURES / "footprints" / f"{fid}.geojson").read_text())
    return shapely.from_geojson(json.dumps(data["features"][0]["geometry"]))


def test_every_fixture_solves() -> None:
    index = json.loads((FIXTURES / "index.json").read_text())
    for footprint in index["footprints"]:
        fid = footprint["footprint_id"]
        poly = _footprint(fid)
        pts = clip_footprint(read_points(FIXTURES / "points" / f"{fid}.laz"), poly).roof.xyz
        detection = detect_planes(pts)
        result = solve_topology(pts, detection.planes, poly)
        assert result.status is TopologyStatus.OK, (fid, result.reason)
        assert len(result.planes) == len(detection.planes)
        for (_, before), (_, after) in zip(detection.planes, result.planes, strict=True):
            assert np.array_equal(before, after)
