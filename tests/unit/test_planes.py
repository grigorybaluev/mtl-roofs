"""Plane fitting must recover a known plane from noisy points."""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mtl_roofs.geometry.planes import Plane, fit_plane, ransac_planes
from tests.conftest import points_on_plane

FINITE = {"allow_nan": False, "allow_infinity": False}


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
