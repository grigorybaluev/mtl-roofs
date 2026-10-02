"""Joint refinement of a footprint's roof planes (#19, ADR 0006).

Detected planes are fitted face by face. This module refines them together by
Levenberg-Marquardt, so that they also satisfy what an LOD2 roof is expected to
satisfy, where the data already nearly does: flat faces level, slopes aligned with
the footprint, ridges level, gables symmetric, and four or more faces meeting at a
corner through one point. The specification is ``docs/topology-solver.md``; this
module follows it, and the names below match its symbols.

In brief:

* Coordinates are shifted to a local origin, and each plane pivots about its own
  inlier centroid: ``n · (x - c) = t``.
* Each normal is parameterised in a tangent basis ``(u, v)`` at its fitted value, so
  ``|n| = 1`` holds by construction and each plane has exactly three unknowns.
* Point evidence enters as the fit's information matrix, inflated by a systematic
  floor (``sigma_angle_deg``, ``sigma_offset_m``); the LiDAR's formal precision
  claims far more than the roofs bear out.
* Priors apply only where the fitted planes satisfy them within a gate, decided once
  before solving.
* Adjacent planes within ``step_angle_deg`` of parallel are steps, not intersections.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import numpy.typing as npt
import shapely
from scipy.spatial import cKDTree

from mtl_roofs.geometry.planes import Plane, fit_plane

log = logging.getLogger(__name__)

FloatArray = npt.NDArray[np.float64]
IndexArray = npt.NDArray[np.intp]


@dataclass(frozen=True, slots=True)
class TopologyParams:
    """Solver parameters, overridable per study area under ``topology:``.

    Attributes:
        sigma_angle_deg: Systematic floor on a fitted plane's orientation.
        sigma_offset_m: Systematic floor on a fitted plane's offset.
        min_point_sigma_m: Floor on the per-point residual used for the evidence.
        prior_sigma_deg: Standard deviation of every prior residual.
        level_gate_deg: Faces at most this steep get the level prior; 0 disables it.
        azimuth_gate_deg: Largest azimuth offset from a footprint direction that gets
            the alignment prior; 0 disables it.
        ridge_gate_deg: Largest ridge tilt that gets the level-ridge prior.
        symmetry_gate_deg: Largest slope difference of a ridge pair that gets the
            symmetry prior.
        min_edge_m: Footprint edges shorter than this give no direction.
        opposed_tolerance_deg: Two faces are opposed (a ridge pair) if their
            azimuths are within this of 180° apart.
        step_angle_deg: Adjacent faces closer than this to parallel are a step.
        adjacency_radius_m: Faces whose inliers come this close in plan are adjacent.
        min_adjacent_points: Points of one face that must lie within the radius of
            the other for the pair to be adjacent.
        corner_tol_m: Triple intersections of one corner must agree to within this.
        min_corner_det: Smallest ``|det|`` of three normals for their intersection
            to be used.
        corner_sigma_m: Standard deviation of a corner residual.
        sliver_width_m: Faces narrower than this across get a ``sliver-face`` warning.
        conflict_sigma: A plane ending further than this many evidence standard
            deviations from its fit gets a ``prior-conflict`` warning.
        lm_tau: Initial damping, relative to the largest diagonal of ``JᵀJ``.
        max_iterations: Iterations before ``no-convergence``.
        gtol: Converged when the largest gradient component is at most this.
        xtol: Converged when the step is at most ``xtol (|x| + xtol)``.
        rcond_min: Below this reciprocal condition number of ``JᵀJ`` at the solution,
            the result is ``ill-conditioned``.
    """

    sigma_angle_deg: float = 1.0
    sigma_offset_m: float = 0.02
    min_point_sigma_m: float = 0.02
    prior_sigma_deg: float = 0.1
    level_gate_deg: float = 3.0
    azimuth_gate_deg: float = 5.0
    ridge_gate_deg: float = 3.0
    symmetry_gate_deg: float = 3.0
    min_edge_m: float = 2.0
    opposed_tolerance_deg: float = 30.0
    step_angle_deg: float = 10.0
    adjacency_radius_m: float = 0.75
    min_adjacent_points: int = 3
    corner_tol_m: float = 0.5
    min_corner_det: float = 0.1
    corner_sigma_m: float = 0.01
    sliver_width_m: float = 0.5
    conflict_sigma: float = 5.0
    lm_tau: float = 1e-3
    max_iterations: int = 100
    gtol: float = 1e-8
    xtol: float = 1e-10
    rcond_min: float = 1e-12


class TopologyStatus(Enum):
    """Outcome for one footprint. Values match the failure log statuses."""

    OK = "ok"
    SOLVER_FAILED = "solver-failed"


class FailureReason(Enum):
    """Why the solver failed; recorded with ``solver-failed``."""

    NO_CONVERGENCE = "no-convergence"
    ILL_CONDITIONED = "ill-conditioned"


class WarningKind(Enum):
    """Conditions that qualify a result without failing it."""

    SLIVER_FACE = "sliver-face"
    ILL_CONDITIONED_CORNER = "ill-conditioned-corner"
    PRIOR_CONFLICT = "prior-conflict"


class RelationKind(Enum):
    """How two adjacent faces meet."""

    RIDGE = "ridge"  # opposed slopes
    BREAK = "break"  # any other intersection: hip, valley, change of slope
    STEP = "step"  # near-parallel: joined by a vertical face, not an intersection


@dataclass(frozen=True, slots=True)
class TopologyWarning:
    """One warning and the planes it concerns (indices into the input planes)."""

    kind: WarningKind
    planes: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Relation:
    """Two adjacent planes and how they meet."""

    a: int
    b: int
    kind: RelationKind


@dataclass(frozen=True, slots=True)
class Corner:
    """Four or more planes meeting at one point, in EPSG:2950."""

    planes: tuple[int, ...]
    point: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class TopologyResult:
    """Refined planes of one footprint, with how they relate and how the solve went.

    Attributes:
        planes: ``(plane, indices)`` in the input order, with the input indices.
        status: ``ok`` or ``solver-failed``.
        reason: Set only when the solver failed.
        warnings: Conditions that did not fail the footprint.
        relations: Every adjacent pair of planes, classified.
        corners: Corners of four or more planes, as solved.
        iterations: LM iterations taken.
        initial_cost: ``½|r|²`` at the fitted planes.
        final_cost: ``½|r|²`` at the solution.
        gradient_norm: ``|Jᵀr|∞`` at the solution.
        rcond: Reciprocal condition number of ``JᵀJ`` at the solution.
        rotation_deg: Per plane, the angle between its fitted and refined normal.
        rms_before: Per plane, RMS distance of its inliers to the fitted plane.
        rms_after: Per plane, the same against the refined plane.
    """

    planes: list[tuple[Plane, IndexArray]]
    status: TopologyStatus = TopologyStatus.OK
    reason: FailureReason | None = None
    warnings: list[TopologyWarning] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    corners: list[Corner] = field(default_factory=list)
    iterations: int = 0
    initial_cost: float = 0.0
    final_cost: float = 0.0
    gradient_norm: float = 0.0
    rcond: float = 1.0
    rotation_deg: list[float] = field(default_factory=list)
    rms_before: list[float] = field(default_factory=list)
    rms_after: list[float] = field(default_factory=list)


# ------------------------------------------------------------------ the problem
@dataclass(slots=True)
class _Face:
    """One plane in local coordinates, with its chart and evidence."""

    centroid: FloatArray
    normal0: FloatArray
    basis: FloatArray  # (3, 2): u, v
    evidence: FloatArray  # (3, 3): (H_eff)^½ on (a, b, t)
    points: FloatArray  # inliers, local coordinates
    width: float


def _slope(n: FloatArray) -> float:
    return float(np.arctan2(np.hypot(n[0], n[1]), n[2]))


def _angle(a: FloatArray, b: FloatArray) -> float:
    """Unsigned angle between two lines, in radians; ``atan2`` stays accurate near 0."""
    return float(np.arctan2(np.linalg.norm(np.cross(a, b)), abs(float(np.dot(a, b)))))


def _tangent_basis(n: FloatArray) -> FloatArray:
    seed = np.eye(3)[int(np.argmin(np.abs(n)))]
    u = np.cross(n, seed)
    u /= np.linalg.norm(u)
    v = np.cross(n, u)
    basis: FloatArray = np.column_stack([u, v])
    return basis


def _psd_sqrt(m: FloatArray) -> FloatArray:
    w, q = np.linalg.eigh(m)
    root: FloatArray = (q * np.sqrt(np.clip(w, 0.0, None))) @ q.T
    return root


def _face(points: FloatArray, p: TopologyParams) -> _Face:
    """Fit a face and build its tangent chart and inflated evidence (spec: Point evidence)."""
    plane = fit_plane(points)
    n0 = np.asarray(plane.normal)
    c = points.mean(axis=0)
    q = points - c
    basis = _tangent_basis(n0)
    sigma_p = max(float(np.sqrt(np.mean((q @ n0) ** 2))), p.min_point_sigma_m)
    inplane = basis.T @ (q.T @ q) @ basis
    w, vecs = np.linalg.eigh(inplane)
    sa2 = np.radians(p.sigma_angle_deg) ** 2
    h_angle = w / sigma_p**2
    h_angle_eff = h_angle / (1.0 + sa2 * h_angle)
    h_offset = len(points) / sigma_p**2
    h_offset_eff = h_offset / (1.0 + p.sigma_offset_m**2 * h_offset)
    evidence = np.zeros((3, 3))
    evidence[:2, :2] = _psd_sqrt((vecs * h_angle_eff) @ vecs.T)
    evidence[2, 2] = np.sqrt(h_offset_eff)
    width = float(np.sqrt(12.0 * max(w[0], 0.0) / len(points)))
    return _Face(c, n0, basis, evidence, points, width)


def _normal(face: _Face, ab: FloatArray) -> tuple[FloatArray, FloatArray]:
    """Normal at chart coordinates ``ab``, and its (3, 2) derivative."""
    m = face.normal0 + face.basis @ ab
    length = float(np.linalg.norm(m))
    n = m / length
    dn: FloatArray = (np.eye(3) - np.outer(n, n)) @ face.basis / length
    return n, dn


@dataclass(slots=True)
class _Problem:
    faces: list[_Face]
    level: list[int]
    aligned: list[tuple[int, FloatArray]]  # plane, target downslope direction (2D unit)
    ridges: list[tuple[int, int]]
    symmetric: list[tuple[int, int]]
    corners: list[tuple[int, ...]]
    sigma_prior: float
    sigma_corner: float

    @property
    def n_params(self) -> int:
        return 3 * len(self.faces) + 3 * len(self.corners)

    def residuals(self, x: FloatArray) -> tuple[FloatArray, FloatArray]:
        """Stacked residuals and their Jacobian at ``x``."""
        nf = len(self.faces)
        normals, dnormals = [], []
        for i, face in enumerate(self.faces):
            n, dn = _normal(face, x[3 * i : 3 * i + 2])
            normals.append(n)
            dnormals.append(dn)
        rows: list[float] = []
        jac: list[FloatArray] = []

        def row(value: float) -> FloatArray:
            rows.append(value)
            r = np.zeros(self.n_params)
            jac.append(r)
            return r

        def via_normal(r: FloatArray, i: int, d_dn: FloatArray) -> None:
            r[3 * i : 3 * i + 2] += d_dn @ dnormals[i]

        for i, face in enumerate(self.faces):
            values = face.evidence @ x[3 * i : 3 * i + 3]
            for k in range(3):
                r = row(float(values[k]))
                r[3 * i : 3 * i + 3] = face.evidence[k]

        s = self.sigma_prior
        for i in self.level:
            for axis in (0, 1):
                r = row(float(normals[i][axis]) / s)
                via_normal(r, i, np.eye(3)[axis] / s)

        for i, e in self.aligned:
            n = normals[i]
            h = n[:2]
            hn = float(np.linalg.norm(h))
            cross = float(h[0] * e[1] - h[1] * e[0])
            d_h = (np.array([e[1], -e[0]]) / hn - cross * h / hn**3) / s
            r = row(cross / hn / s)
            via_normal(r, i, np.array([d_h[0], d_h[1], 0.0]))

        for i, j in self.ridges:
            ni, nj = normals[i], normals[j]
            w = np.cross(ni, nj)
            wn = float(np.linalg.norm(w))
            d_wz_i = np.array([nj[1], -nj[0], 0.0])
            d_wz_j = np.array([-ni[1], ni[0], 0.0])
            d_wn_i = np.cross(nj, w) / wn
            d_wn_j = np.cross(w, ni) / wn
            r = row(float(w[2]) / wn / s)
            via_normal(r, i, (d_wz_i * wn - w[2] * d_wn_i) / wn**2 / s)
            via_normal(r, j, (d_wz_j * wn - w[2] * d_wn_j) / wn**2 / s)

        for i, j in self.symmetric:
            r = row((_slope(normals[i]) - _slope(normals[j])) / s)
            via_normal(r, i, _d_slope(normals[i]) / s)
            via_normal(r, j, -_d_slope(normals[j]) / s)

        for k, members in enumerate(self.corners):
            point = x[3 * nf + 3 * k : 3 * nf + 3 * k + 3]
            for i in members:
                face = self.faces[i]
                offset = point - face.centroid
                r = row((float(normals[i] @ offset) - x[3 * i + 2]) / self.sigma_corner)
                via_normal(r, i, offset / self.sigma_corner)
                r[3 * i + 2] -= 1.0 / self.sigma_corner
                r[3 * nf + 3 * k : 3 * nf + 3 * k + 3] = normals[i] / self.sigma_corner

        return np.asarray(rows, dtype=np.float64), np.asarray(jac, dtype=np.float64).reshape(
            len(rows), self.n_params
        )


def _d_slope(n: FloatArray) -> FloatArray:
    """Derivative of the slope angle ``atan2(|h|, n_z)`` with respect to ``n``."""
    h = float(np.hypot(n[0], n[1]))
    q = h * h + n[2] * n[2]
    if h == 0.0:
        return np.zeros(3)
    return np.array([n[2] * n[0] / h / q, n[2] * n[1] / h / q, -h / q])


# ----------------------------------------------------------------- relations
def _adjacent(faces: list[_Face], p: TopologyParams) -> list[tuple[int, int]]:
    trees = [cKDTree(f.points[:, :2]) for f in faces]
    pairs = []
    for a, b in itertools.combinations(range(len(faces)), 2):
        d, _ = trees[a].query(
            faces[b].points[:, :2], k=1, distance_upper_bound=p.adjacency_radius_m
        )
        if int(np.isfinite(d).sum()) >= p.min_adjacent_points:
            pairs.append((a, b))
    return pairs


def _classify(fa: _Face, fb: _Face, p: TopologyParams) -> RelationKind:
    na, nb = fa.normal0, fb.normal0
    if np.degrees(_angle(na, nb)) < p.step_angle_deg:
        return RelationKind.STEP
    ha, hb = na[:2], nb[:2]
    if np.hypot(*ha) == 0.0 or np.hypot(*hb) == 0.0:
        return RelationKind.BREAK
    between = np.degrees(
        np.arccos(np.clip(ha @ hb / np.linalg.norm(ha) / np.linalg.norm(hb), -1, 1))
    )
    if 180.0 - between <= p.opposed_tolerance_deg:
        return RelationKind.RIDGE
    return RelationKind.BREAK


def _edge_directions(footprint: shapely.Polygon, p: TopologyParams) -> FloatArray:
    """Unit directions of the footprint's long edges and their perpendiculars, both senses."""
    dirs = []
    for ring in [footprint.exterior, *footprint.interiors]:
        coords = np.asarray(ring.coords)[:, :2]
        for e in np.diff(coords, axis=0):
            length = float(np.hypot(*e))
            if length >= p.min_edge_m:
                d = e / length
                perp = np.array([-d[1], d[0]])
                dirs.extend([d, -d, perp, -perp])
    return np.asarray(dirs, dtype=np.float64).reshape(-1, 2)


def _triple_point(faces: list[_Face], triple: tuple[int, ...]) -> tuple[FloatArray, float]:
    normals = np.array([faces[i].normal0 for i in triple])
    det = float(abs(np.linalg.det(normals)))
    if det == 0.0:
        return np.full(3, np.nan), 0.0
    rhs = np.array([faces[i].normal0 @ faces[i].centroid for i in triple])
    return np.linalg.solve(normals, rhs), det


def _corners(
    faces: list[_Face],
    adjacency: set[tuple[int, int]],
    region: shapely.Geometry,
    origin: FloatArray,
    p: TopologyParams,
    warnings: list[TopologyWarning],
) -> list[tuple[int, ...]]:
    """Corners of four or more planes (spec: Corners).

    A set of faces around a point that the well-conditioned triples locate, but that
    holds an ill-conditioned triple, is a corner the solver cannot use: it is skipped
    with an ``ill-conditioned-corner`` warning. An ill-conditioned triple elsewhere is
    ordinary geometry (a mansard's faces meet along parallel lines) and is not reported.
    """

    def around(group: tuple[int, ...]) -> bool:
        # Faces round a corner each touch the two on either side; opposite faces of a
        # pyramid meet only at the apex, so they need not be adjacent themselves.
        return all(
            sum((min(a, b), max(a, b)) in adjacency for b in group if b != a) >= 2 for a in group
        )

    trees = [cKDTree(f.points[:, :2]) for f in faces]

    def locate(points: list[FloatArray], group: tuple[int, ...]) -> FloatArray | None:
        """The common point of the triple intersections, if they agree and lie on the roof."""
        if len(points) < 2:
            return None
        stack = np.array(points)
        spread = max(float(np.linalg.norm(a - b)) for a, b in itertools.combinations(stack, 2))
        centre: FloatArray = stack.mean(axis=0)
        if spread > p.corner_tol_m:
            return None
        if not region.contains(shapely.Point(*(centre[:2] + origin[:2]))):
            return None
        if any(trees[i].query(centre[:2])[0] > p.corner_tol_m for i in group):
            return None
        return centre

    found: list[tuple[set[int], FloatArray]] = []
    for quad in itertools.combinations(range(len(faces)), 4):
        if not around(quad):
            continue
        good, ill = [], False
        for triple in itertools.combinations(quad, 3):
            point, det = _triple_point(faces, triple)
            if det < p.min_corner_det:
                ill = True
            else:
                good.append(point)
        centre = locate(good, quad)
        if centre is None:
            continue
        if ill:
            warnings.append(TopologyWarning(WarningKind.ILL_CONDITIONED_CORNER, quad))
            continue
        for members, at in found:
            if len(members & set(quad)) >= 3 and np.linalg.norm(at - centre) <= p.corner_tol_m:
                members.update(quad)
                break
        else:
            found.append((set(quad), centre))
    return [tuple(sorted(members)) for members, _ in found]


def _build(
    faces: list[_Face], footprint: shapely.Polygon, origin: FloatArray, p: TopologyParams
) -> tuple[_Problem, list[Relation], list[TopologyWarning]]:
    warnings: list[TopologyWarning] = []
    pairs = _adjacent(faces, p)
    relations = [Relation(a, b, _classify(faces[a], faces[b], p)) for a, b in pairs]
    level_gate = np.radians(p.level_gate_deg)
    prior = np.radians(p.prior_sigma_deg)

    level = [
        i for i, f in enumerate(faces) if p.level_gate_deg > 0 and _slope(f.normal0) <= level_gate
    ]

    aligned: list[tuple[int, FloatArray]] = []
    directions = _edge_directions(footprint, p)
    if p.azimuth_gate_deg > 0 and len(directions):
        for i, f in enumerate(faces):
            if i in level:
                continue
            h = f.normal0[:2]
            if np.hypot(*h) == 0.0:
                continue
            down = h / np.linalg.norm(h)
            offsets = np.degrees(np.arccos(np.clip(directions @ down, -1.0, 1.0)))
            k = int(np.argmin(offsets))
            if offsets[k] <= p.azimuth_gate_deg:
                aligned.append((i, directions[k]))

    ridges, symmetric = [], []
    for rel in relations:
        if rel.kind is not RelationKind.RIDGE:
            continue
        na, nb = faces[rel.a].normal0, faces[rel.b].normal0
        line = np.cross(na, nb)
        tilt = np.degrees(np.arcsin(min(1.0, abs(line[2]) / np.linalg.norm(line))))
        if tilt <= p.ridge_gate_deg:
            ridges.append((rel.a, rel.b))
        if np.degrees(abs(_slope(na) - _slope(nb))) <= p.symmetry_gate_deg:
            symmetric.append((rel.a, rel.b))

    linked = {(r.a, r.b) for r in relations if r.kind is not RelationKind.STEP}
    region = footprint.buffer(p.corner_tol_m)
    corners = _corners(faces, linked, region, origin, p, warnings)

    for i, f in enumerate(faces):
        if f.width < p.sliver_width_m:
            warnings.append(TopologyWarning(WarningKind.SLIVER_FACE, (i,)))

    problem = _Problem(faces, level, aligned, ridges, symmetric, corners, prior, p.corner_sigma_m)
    return problem, relations, warnings


# ----------------------------------------------------------------- the solver
@dataclass(frozen=True, slots=True)
class _LMResult:
    x: FloatArray
    converged: bool
    iterations: int
    initial_cost: float
    final_cost: float
    gradient_norm: float
    rcond: float


def _levenberg_marquardt(problem: _Problem, x0: FloatArray, p: TopologyParams) -> _LMResult:
    """Minimise ``½|r|²`` with Nielsen's damping update (spec: Levenberg-Marquardt)."""
    x = x0.copy()
    r, jac = problem.residuals(x)
    cost = 0.5 * float(r @ r)
    initial = cost
    a = jac.T @ jac
    g = jac.T @ r
    diag = np.diag(a).copy()
    lam = p.lm_tau * float(diag.max()) if diag.size and diag.max() > 0 else p.lm_tau
    nu = 2.0
    converged = False
    iterations = 0
    while iterations < p.max_iterations:
        if float(np.max(np.abs(g), initial=0.0)) <= p.gtol:
            converged = True
            break
        iterations += 1
        scale = np.maximum(diag, 1e-12 * max(float(diag.max()), 1.0))
        damped = a + lam * np.diag(scale)
        try:
            step = np.linalg.solve(damped, -g)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(damped, -g, rcond=None)[0]
        if float(np.linalg.norm(step)) <= p.xtol * (float(np.linalg.norm(x)) + p.xtol):
            converged = True
            break
        r_new, jac_new = problem.residuals(x + step)
        cost_new = 0.5 * float(r_new @ r_new)
        # Decrease predicted by the linear model: ½ δᵀ(λDδ - g), positive for any δ.
        predicted = 0.5 * float(step @ (lam * scale * step - g))
        rho = (cost - cost_new) / predicted if predicted > 0 else -1.0
        if rho > 0:
            x = x + step
            r, jac, cost = r_new, jac_new, cost_new
            a = jac.T @ jac
            g = jac.T @ r
            diag = np.diag(a).copy()
            lam *= max(1.0 / 3.0, 1.0 - (2.0 * rho - 1.0) ** 3)
            nu = 2.0
        else:
            lam *= nu
            nu *= 2.0
    else:
        converged = float(np.max(np.abs(g), initial=0.0)) <= p.gtol

    eig = np.linalg.eigvalsh(a) if a.size else np.ones(1)
    rcond = float(eig[0] / eig[-1]) if eig[-1] > 0 else 0.0
    return _LMResult(
        x=x,
        converged=converged,
        iterations=iterations,
        initial_cost=initial,
        final_cost=cost,
        gradient_norm=float(np.max(np.abs(g), initial=0.0)),
        rcond=rcond,
    )


def solve_topology(
    points: FloatArray,
    planes: list[tuple[Plane, IndexArray]],
    footprint: shapely.Polygon,
    params: TopologyParams | None = None,
) -> TopologyResult:
    """Refine one footprint's planes jointly (``docs/topology-solver.md``).

    Args:
        points: ``(n, 3)`` roof points, as given to plane detection.
        planes: ``(plane, indices)`` from :func:`~mtl_roofs.geometry.planes.detect_planes`.
            Each plane is refitted to its inliers; the given parameters are not used.
        footprint: The footprint polygon, in EPSG:2950. Its long edges give the
            directions slopes are aligned to.
        params: Defaults to :class:`TopologyParams`.

    Returns:
        The refined planes in the input order, with status, relations, corners and
        diagnostics. A failed solve still returns the planes it reached, so the
        failure log can record them; they are not for use.
    """
    p = params or TopologyParams()
    pts = np.asarray(points, dtype=np.float64)
    if not planes:
        return TopologyResult(planes=[])
    origin = np.mean(np.concatenate([pts[idx] for _, idx in planes]), axis=0)
    local = pts - origin
    faces = [_face(local[idx], p) for _, idx in planes]
    problem, relations, warnings = _build(faces, footprint, origin, p)

    x0 = np.zeros(problem.n_params)
    nf = len(faces)
    for k, members in enumerate(problem.corners):
        normals = np.array([faces[i].normal0 for i in members])
        rhs = np.array([faces[i].normal0 @ faces[i].centroid for i in members])
        x0[3 * nf + 3 * k : 3 * nf + 3 * k + 3] = np.linalg.lstsq(normals, rhs, rcond=None)[0]
    solved = _levenberg_marquardt(problem, x0, p)
    x = solved.x

    refined: list[tuple[Plane, IndexArray]] = []
    rotation, before, after = [], [], []
    for i, (face, (_, idx)) in enumerate(zip(faces, planes, strict=True)):
        n, _ = _normal(face, x[3 * i : 3 * i + 2])
        t = float(x[3 * i + 2])
        q = face.points - face.centroid
        before.append(float(np.sqrt(np.mean((q @ face.normal0) ** 2))))
        after.append(float(np.sqrt(np.mean((q @ n - t) ** 2))))
        rotation.append(float(np.degrees(_angle(n, face.normal0))))
        d = float(n @ (face.centroid + origin)) + t
        if n[2] < 0:
            n, d = -n, -d
        refined.append((Plane((float(n[0]), float(n[1]), float(n[2])), d), idx))
        if float(np.linalg.norm(face.evidence @ x[3 * i : 3 * i + 3])) > p.conflict_sigma:
            warnings.append(TopologyWarning(WarningKind.PRIOR_CONFLICT, (i,)))

    corners = [
        Corner(members, tuple(float(c) for c in x[3 * nf + 3 * k : 3 * nf + 3 * k + 3] + origin))  # type: ignore[arg-type]
        for k, members in enumerate(problem.corners)
    ]

    reason: FailureReason | None = None
    if not solved.converged:
        reason = FailureReason.NO_CONVERGENCE
    elif solved.rcond < p.rcond_min:
        reason = FailureReason.ILL_CONDITIONED
    status = TopologyStatus.OK if reason is None else TopologyStatus.SOLVER_FAILED

    result = TopologyResult(
        planes=refined,
        status=status,
        reason=reason,
        warnings=warnings,
        relations=relations,
        corners=corners,
        iterations=solved.iterations,
        initial_cost=solved.initial_cost,
        final_cost=solved.final_cost,
        gradient_norm=solved.gradient_norm,
        rcond=solved.rcond,
        rotation_deg=rotation,
        rms_before=before,
        rms_after=after,
    )
    log.info(
        "topology: status=%s reason=%s planes=%d corners=%d iterations=%d cost=%.3g->%.3g "
        "warnings=%s",
        status.value,
        reason.value if reason else "-",
        len(refined),
        len(corners),
        solved.iterations,
        solved.initial_cost,
        solved.final_cost,
        ",".join(sorted({w.kind.value for w in warnings})) or "-",
    )
    return result
