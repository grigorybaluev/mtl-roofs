"""Shared fixtures."""

from __future__ import annotations

import numpy as np
import pytest


@pytest.fixture
def rng() -> np.random.Generator:
    """A seeded generator, so a failing test reproduces exactly."""
    return np.random.default_rng(20260922)


def points_on_plane(
    normal: tuple[float, float, float],
    offset: float,
    count: int,
    rng: np.random.Generator,
    noise: float = 0.0,
    extent: float = 10.0,
) -> np.ndarray:
    """Sample points lying on ``n · x = d``, optionally with Gaussian normal noise."""
    n = np.asarray(normal, dtype=np.float64)
    n = n / np.linalg.norm(n)
    # Build an orthonormal basis of the plane from the least-aligned axis.
    seed = np.eye(3)[int(np.argmin(np.abs(n)))]
    u = np.cross(n, seed)
    u /= np.linalg.norm(u)
    v = np.cross(n, u)
    a = rng.uniform(-extent, extent, count)
    b = rng.uniform(-extent, extent, count)
    pts = offset * n + a[:, None] * u + b[:, None] * v
    if noise > 0:
        pts = pts + rng.normal(0.0, noise, count)[:, None] * n
    return pts
