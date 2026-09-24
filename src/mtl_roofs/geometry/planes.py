"""Robust roof-plane detection and least-squares refinement.

The detector is deliberately two-stage, because the two stages fail differently:
RANSAC finds *which* points belong to a plane but gives a coarse fit driven by the
minimal sample; the total least-squares refinement then uses every inlier and is
what determines the orientation error the evaluation reports.

On real roofs, distance to the plane is not enough to decide membership. Three
further tests come from failures measured on the fixtures (#18):

- **Normal agreement.** A plane at a shallow slope passes within the threshold of
  points on *both* faces of a pitched roof near the ridge, and wins the vote as an
  11-14° "slab" through the ridge. Each point therefore also needs its local normal
  to agree with the plane's.
- **Slope cap.** Facade points inside the footprint form 85-90° planes. The steepest
  reference roof face in the fixtures is 79°, so walls are excluded by slope.
- **Connectivity.** A plane's inliers are split into spatially connected pieces, and
  only the largest one is kept, so coplanar but separate roof sections (a front
  and a rear flat roof at the same height) become separate faces.
- **Depth.** The parapet around a flat roof yields thin 35-45° bands a few decimetres
  wide along the footprint edge, whose normals blend roof and wall. A sloped face
  must extend at least ``min_depth_m`` down its slope.

A candidate that fails the last two tests does not end the search: its points are
set aside as unassigned, and the search continues on the rest.

Two passes follow the search. Points next to a plane and within the distance
threshold, rejected only for a blended normal at a roof edge or ridge, are
*absorbed* by the nearest such plane. Adjacent planes within ``merge_angle_deg`` of
each other whose union still fits within the threshold are then *merged*: a flat
roof sagging a few centimetres towards its drains is one face, not two.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import numpy.typing as npt
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

log = logging.getLogger(__name__)

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


class PlaneStatus(Enum):
    """Outcome of plane detection for one footprint. Values match the failure log statuses."""

    OK = "ok"
    NO_PLANES = "no-planes"


@dataclass(frozen=True, slots=True)
class PlaneParams:
    """Plane detection parameters, overridable per study area.

    Attributes:
        threshold: Inlier distance in metres. The default is roughly the LiDAR's
            stated 20 cm vertical accuracy.
        min_inliers: Planes with fewer points than this are not reported. Matches
            the clipper's ``min_points``.
        max_planes: Stop after this many planes, however many points remain.
        iterations: RANSAC trials per plane.
        normal_threshold_deg: Largest angle between a point's local normal and the
            plane normal for the point to be an inlier.
        normal_neighbours: Points used to estimate each local normal.
        max_slope_deg: Planes steeper than this are walls, not roof.
        connect_radius_m: Inliers closer than this are connected. About three times
            the point spacing at the fixtures' 16-27 pts/m².
        min_depth_m: Smallest extent of a sloped plane down its slope.
        flat_slope_deg: Planes at most this steep are flat, and exempt from
            ``min_depth_m``, since they have no well-defined downslope direction.
        merge_angle_deg: Adjacent planes closer in orientation than this are
            merged if their union fits within ``threshold`` at the 95th percentile.
        seed: Seeds the random generator, so that a run is reproducible.
    """

    threshold: float = 0.15
    min_inliers: int = 50
    max_planes: int = 30
    iterations: int = 200
    normal_threshold_deg: float = 20.0
    normal_neighbours: int = 10
    max_slope_deg: float = 80.0
    connect_radius_m: float = 0.75
    min_depth_m: float = 1.0
    flat_slope_deg: float = 5.0
    merge_angle_deg: float = 5.0
    seed: int = 0


@dataclass(frozen=True, slots=True)
class PlaneDetection:
    """Planes detected on one footprint's roof points.

    Attributes:
        planes: ``(plane, indices)`` pairs, largest first; ``indices`` index into the
            input points. Every point belongs to at most one plane.
        status: ``ok``, or ``no-planes`` when nothing met the inlier threshold.
        seed: The seed the run used, for the run log.
        n_points: Number of input points.
    """

    planes: list[tuple[Plane, IndexArray]] = field(default_factory=list)
    status: PlaneStatus = PlaneStatus.OK
    seed: int = 0
    n_points: int = 0

    @property
    def coverage(self) -> float:
        """Fraction of the input points assigned to a plane."""
        if self.n_points == 0:
            return 0.0
        return sum(len(idx) for _, idx in self.planes) / self.n_points


def detect_planes(points: FloatArray, params: PlaneParams | None = None) -> PlaneDetection:
    """Detect the roof planes of one footprint.

    Args:
        points: ``(n, 3)`` roof points, as returned by the clipper.
        params: Defaults to :class:`PlaneParams`.
    """
    p = params or PlaneParams()
    pts = np.asarray(points, dtype=np.float64)
    planes = ransac_planes(
        pts,
        threshold=p.threshold,
        min_inliers=p.min_inliers,
        max_planes=p.max_planes,
        iterations=p.iterations,
        normal_threshold_deg=p.normal_threshold_deg,
        normal_neighbours=p.normal_neighbours,
        max_slope_deg=p.max_slope_deg,
        connect_radius_m=p.connect_radius_m,
        min_depth_m=p.min_depth_m,
        flat_slope_deg=p.flat_slope_deg,
        merge_angle_deg=p.merge_angle_deg,
        rng=np.random.default_rng(p.seed),
    )
    status = PlaneStatus.OK if planes else PlaneStatus.NO_PLANES
    result = PlaneDetection(planes=planes, status=status, seed=p.seed, n_points=len(pts))
    log.info(
        "plane detection: status=%s planes=%d coverage=%.3f seed=%d",
        status.value,
        len(planes),
        result.coverage,
        p.seed,
    )
    return result


def local_normals(points: FloatArray, k: int = 10) -> FloatArray:
    """Unit normal of each point from the PCA of its ``k`` nearest neighbours.

    Normals are oriented upwards. Where the neighbourhood is degenerate (fewer than
    three points), the normal is NaN and the point agrees with no plane.
    """
    pts = np.asarray(points, dtype=np.float64)
    normals = np.full_like(pts, np.nan)
    k = min(k, len(pts))
    if k < 3:
        return normals
    _, idx = cKDTree(pts).query(pts, k=k)
    neighbourhoods = pts[np.asarray(idx).reshape(len(pts), k)]
    centred = neighbourhoods - neighbourhoods.mean(axis=1, keepdims=True)
    # Smallest eigenvector of each 3x3 covariance; eigh sorts eigenvalues ascending.
    _, vectors = np.linalg.eigh(np.einsum("nki,nkj->nij", centred, centred))
    normals = vectors[:, :, 0]
    normals[normals[:, 2] < 0] *= -1
    result: FloatArray = normals
    return result


def largest_component(points: FloatArray, radius: float) -> IndexArray:
    """Indices of the largest set of ``points`` connected by steps of at most ``radius``."""
    if len(points) == 0:
        return np.empty(0, dtype=np.intp)
    pairs = cKDTree(points).query_pairs(radius, output_type="ndarray")
    n = len(points)
    graph = coo_matrix((np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
    _, labels = connected_components(graph, directed=False)
    largest: IndexArray = np.flatnonzero(labels == np.bincount(labels).argmax())
    return largest


def downslope_depth(plane: Plane, points: FloatArray) -> float:
    """Extent of ``points`` down the slope of ``plane``, from the 5th to 95th percentile.

    Returns ``inf`` for a horizontal plane, which has no downslope direction.
    """
    horizontal = np.hypot(plane.normal[0], plane.normal[1])
    if horizontal == 0.0:
        return float("inf")
    direction = np.array([plane.normal[0], plane.normal[1]]) / horizontal
    along = np.asarray(points)[:, :2] @ direction
    low, high = np.percentile(along, [5, 95])
    return float(high - low)


def ransac_planes(
    points: FloatArray,
    *,
    threshold: float = 0.15,
    min_inliers: int = 50,
    max_planes: int = 30,
    iterations: int = 200,
    normal_threshold_deg: float | None = 20.0,
    normal_neighbours: int = 10,
    max_slope_deg: float = 90.0,
    connect_radius_m: float | None = None,
    min_depth_m: float = 0.0,
    flat_slope_deg: float = 5.0,
    merge_angle_deg: float | None = None,
    rng: np.random.Generator | None = None,
) -> list[tuple[Plane, IndexArray]]:
    """Sequentially extract planes by RANSAC, refining each on its inliers.

    Args:
        points: ``(n, 3)`` array of roof points.
        threshold: Inlier distance in metres.
        min_inliers: Planes smaller than this are not reported.
        max_planes: Stop after this many planes, however many points remain.
        iterations: RANSAC trials per plane.
        normal_threshold_deg: Largest angle between a point's local normal and the
            plane for the point to count as an inlier; ``None`` disables the test.
        normal_neighbours: Neighbours used to estimate local normals.
        max_slope_deg: Candidate planes steeper than this are rejected.
        connect_radius_m: Keep only the largest connected piece of each plane's
            inliers; ``None`` disables the test.
        min_depth_m: Reject a plane steeper than ``flat_slope_deg`` whose inliers
            extend less than this down its slope.
        flat_slope_deg: Planes at most this steep are exempt from ``min_depth_m``.
        merge_angle_deg: Merge adjacent planes closer in orientation than this whose
            union fits within ``threshold``; ``None`` disables merging. Adjacency
            needs ``connect_radius_m``.
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
    min_nz = float(np.cos(np.radians(max_slope_deg)))
    normals = local_normals(pts, normal_neighbours) if normal_threshold_deg is not None else None
    min_dot = float(np.cos(np.radians(normal_threshold_deg or 0.0)))

    def inliers(plane: Plane, idx: IndexArray) -> BoolArray:
        mask: BoolArray = plane.distance(pts[idx]) <= threshold
        if normals is not None:
            # NaN normals compare False, so a degenerate neighbourhood is never an inlier.
            mask &= np.abs(normals[idx] @ np.asarray(plane.normal)) >= min_dot
        return mask

    remaining = np.arange(len(pts))
    found: list[tuple[Plane, IndexArray]] = []

    while len(found) < max_planes and len(remaining) >= min_inliers:
        best: Plane | None = None
        best_count = 0
        subset = pts[remaining]
        for _ in range(iterations):
            sample = generator.choice(len(subset), size=3, replace=False)
            try:
                candidate = fit_plane(subset[sample])
            except ValueError:
                continue
            if candidate.normal[2] < min_nz:
                continue
            count = int(inliers(candidate, remaining).sum())
            if count > best_count:
                best_count, best = count, candidate
        if best is None or best_count < min_inliers:
            break
        # Refine on every inlier, then re-select against the refined plane: the
        # minimal-sample fit that won the vote is systematically worse.
        refined = fit_plane(pts[remaining[inliers(best, remaining)]])
        inlier_idx = remaining[inliers(refined, remaining)]
        candidate_idx = inlier_idx
        if connect_radius_m is not None and len(inlier_idx):
            inlier_idx = inlier_idx[largest_component(pts[inlier_idx], connect_radius_m)]
        plane = fit_plane(pts[inlier_idx]) if len(inlier_idx) >= 3 else None
        if (
            plane is None
            or len(inlier_idx) < min_inliers
            or plane.normal[2] < min_nz
            or (
                plane.slope_degrees > flat_slope_deg
                and downslope_depth(plane, pts[inlier_idx]) < min_depth_m
            )
        ):
            # Set the candidate's points aside rather than stopping: a parapet or a
            # scatter of fragments is no reason to miss the planes still remaining.
            # Each pass removes at least ``min_inliers`` points, so the loop ends.
            remaining = np.setdiff1d(remaining, candidate_idx, assume_unique=True)
            continue
        found.append((plane, np.sort(inlier_idx)))
        remaining = np.setdiff1d(remaining, inlier_idx, assume_unique=True)

    if connect_radius_m is not None and found:
        found = _absorb(pts, found, threshold, connect_radius_m)
        if merge_angle_deg is not None:
            found = _merge(pts, found, threshold, connect_radius_m, merge_angle_deg)
    return sorted(found, key=lambda pair: -len(pair[1]))


def _absorb(
    pts: FloatArray, found: list[tuple[Plane, IndexArray]], threshold: float, radius: float
) -> list[tuple[Plane, IndexArray]]:
    """Give each unassigned point to the nearest plane it is close to and touches."""
    assigned = np.zeros(len(pts), dtype=bool)
    for _, idx in found:
        assigned[idx] = True
    free = np.flatnonzero(~assigned)
    if len(free) == 0:
        return found
    distance = np.full((len(found), len(free)), np.inf)
    for k, (plane, idx) in enumerate(found):
        touching, _ = cKDTree(pts[idx]).query(pts[free], k=1, distance_upper_bound=radius)
        d = plane.distance(pts[free])
        ok = np.isfinite(touching) & (d <= threshold)
        distance[k, ok] = d[ok]
    owner = distance.argmin(axis=0)
    claimed = np.isfinite(distance.min(axis=0))
    result: list[tuple[Plane, IndexArray]] = []
    for k, (_, idx) in enumerate(found):
        grown = np.sort(np.concatenate([idx, free[claimed & (owner == k)]]))
        result.append((fit_plane(pts[grown]), grown))
    return result


def _merge(
    pts: FloatArray,
    found: list[tuple[Plane, IndexArray]],
    threshold: float,
    radius: float,
    max_angle_deg: float,
) -> list[tuple[Plane, IndexArray]]:
    """Repeatedly merge the best-fitting adjacent pair of near-parallel planes."""
    planes = list(found)
    while True:
        best: tuple[float, int, int, Plane, IndexArray] | None = None
        for a in range(len(planes)):
            for b in range(a + 1, len(planes)):
                (pa, ia), (pb, ib) = planes[a], planes[b]
                if pa.angle_to(pb) > max_angle_deg:
                    continue
                gap, _ = cKDTree(pts[ia]).query(pts[ib], k=1, distance_upper_bound=radius)
                if not np.isfinite(gap).any():
                    continue
                union = np.sort(np.concatenate([ia, ib]))
                merged = fit_plane(pts[union])
                spread = float(np.percentile(merged.distance(pts[union]), 95))
                if spread <= threshold and (best is None or spread < best[0]):
                    best = (spread, a, b, merged, union)
        if best is None:
            return planes
        _, a, b, merged, union = best
        planes = [pair for k, pair in enumerate(planes) if k not in (a, b)]
        planes.append((merged, union))
