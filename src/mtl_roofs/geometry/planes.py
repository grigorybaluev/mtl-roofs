"""Robust roof-plane detection and least-squares refinement.

The detector is deliberately two-stage, because the two stages fail differently:
RANSAC finds *which* points belong to a plane but gives a coarse fit driven by the
minimal sample; the total least-squares refinement then uses every inlier and is
what determines the orientation error the evaluation reports.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]
IndexArray = npt.NDArray[np.intp]


@dataclass(frozen=True, slots=True)
class Plane:
    """A plane in Hessian normal form: ``n · x = d`` with ``|n| = 1``.

    The normal is canonically oriented upwards (``nz >= 0``) so that two fits of the
    same roof surface are always directly comparable.
    """

    normal: tuple[float, float, float]
    offset: float

    def __post_init__(self) -> None:
        """Reject a degenerate normal at construction, not at first use."""
        norm = float(np.linalg.norm(self.normal))
        if not np.isfinite(norm) or norm == 0.0:
            msg = f"degenerate plane normal {self.normal!r}"
            raise ValueError(msg)

    @property
    def slope_degrees(self) -> float:
        """Angle between the plane normal and vertical, in degrees."""
        return float(np.degrees(np.arccos(min(1.0, abs(self.normal[2])))))

    def distance(self, points: FloatArray) -> FloatArray:
        """Absolute orthogonal distance from each point to the plane."""
        pts = np.atleast_2d(np.asarray(points, dtype=np.float64))
        distances: FloatArray = np.abs(pts @ np.asarray(self.normal) - self.offset)
        return distances

    def angle_to(self, other: Plane) -> float:
        """Angle between this plane and ``other``, in degrees, in ``[0, 90]``."""
        dot = abs(float(np.dot(self.normal, other.normal)))
        return float(np.degrees(np.arccos(min(1.0, dot))))


def _canonical(normal: FloatArray, offset: float) -> tuple[tuple[float, float, float], float]:
    """Flip a normal so it points up, keeping the plane equation consistent."""
    if normal[2] < 0:
        normal, offset = -normal, -offset
    return (float(normal[0]), float(normal[1]), float(normal[2])), float(offset)


def fit_plane(points: FloatArray) -> Plane:
    """Fit a plane by total least squares (orthogonal regression).

    Uses the SVD of the centred coordinates, so the fit minimises orthogonal
    distance rather than vertical residual. That matters for steep roof faces,
    where a ``z = ax + by + c`` fit is badly conditioned and degenerate for walls.

    Args:
        points: ``(n, 3)`` array of coordinates, ``n >= 3``.

    Raises:
        ValueError: if fewer than three points are given, or they are collinear.
    """
    pts = np.asarray(points, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 3:
        msg = f"expected an (n, 3) array, got shape {pts.shape}"
        raise ValueError(msg)
    if len(pts) < 3:
        msg = f"need at least 3 points to fit a plane, got {len(pts)}"
        raise ValueError(msg)

    centroid = pts.mean(axis=0)
    _u, singular, vh = np.linalg.svd(pts - centroid, full_matrices=False)
    if singular[1] <= 1e-12 * max(singular[0], 1.0):
        msg = "points are collinear or coincident; no unique plane"
        raise ValueError(msg)
    normal = vh[2]
    offset = float(np.dot(normal, centroid))
    canonical_normal, canonical_offset = _canonical(normal, offset)
    return Plane(canonical_normal, canonical_offset)


def ransac_planes(
    points: FloatArray,
    *,
    threshold: float = 0.15,
    min_inliers: int = 50,
    max_planes: int = 12,
    iterations: int = 200,
    rng: np.random.Generator | None = None,
) -> list[tuple[Plane, IndexArray]]:
    """Sequentially extract planes by RANSAC, refining each on its inliers.

    Args:
        points: ``(n, 3)`` array of roof points.
        threshold: Inlier distance in metres. The default is roughly the LiDAR's
            stated 20 cm vertical accuracy.
        min_inliers: Planes smaller than this are not reported.
        max_planes: Stop after this many planes, however many points remain.
        iterations: RANSAC trials per plane.
        rng: Seeded generator; pass one to make a run reproducible.

    Returns:
        ``(plane, indices)`` pairs, largest plane first, where ``indices`` index into
        the original ``points`` array.
    """
    pts = np.asarray(points, dtype=np.float64)
    if pts.ndim != 2 or pts.shape[1] != 3:
        msg = f"expected an (n, 3) array, got shape {pts.shape}"
        raise ValueError(msg)
    generator = rng if rng is not None else np.random.default_rng()

    remaining = np.arange(len(pts))
    found: list[tuple[Plane, IndexArray]] = []

    while len(found) < max_planes and len(remaining) >= min_inliers:
        best_mask: BoolArray | None = None
        best_count = 0
        subset = pts[remaining]
        for _ in range(iterations):
            sample = generator.choice(len(subset), size=3, replace=False)
            try:
                candidate = fit_plane(subset[sample])
            except ValueError:
                continue
            mask = candidate.distance(subset) <= threshold
            count = int(mask.sum())
            if count > best_count:
                best_count, best_mask = count, mask
        if best_mask is None or best_count < min_inliers:
            break
        inlier_idx = remaining[best_mask]
        refined = fit_plane(pts[inlier_idx])
        # Re-select inliers against the refined plane: the minimal-sample fit that
        # won the vote is systematically worse than the least-squares fit.
        final_mask = refined.distance(pts[inlier_idx]) <= threshold
        inlier_idx = inlier_idx[final_mask]
        if len(inlier_idx) < min_inliers:
            break
        found.append((fit_plane(pts[inlier_idx]), inlier_idx))
        remaining = np.setdiff1d(remaining, inlier_idx, assume_unique=True)

    return found
