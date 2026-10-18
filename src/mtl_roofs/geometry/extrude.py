"""Extrusion of a partitioned roof to a watertight LOD2 solid.

The solid is a 2.5D block: every point of the footprint is roofed by exactly one face
of :func:`~mtl_roofs.geometry.roof.partition_roof`, walls are vertical, and the floor
is the footprint at ground height. Its surfaces are:

* **roof**: each face of the partition, its vertices lifted onto the face's plane;
* **wall**: on the footprint outline, from the ground up to the roof; inside it,
  wherever two neighbouring faces are not at the same height along their shared edge
  (a step between near-parallel planes, or a boundary that could not be snapped to
  the planes' intersection);
* **ground**: the footprint at ``ground_z``.

All vertices are shared, so closure is checked topologically, by the edge-parity test
:func:`is_watertight`, and orientation by :func:`is_consistently_oriented` and a
positive volume. Rings follow ``CLAUDE.md``: exterior rings counter-clockwise seen from
outside the solid, holes clockwise, normals outward.

Three things need care to keep the mesh closed:

* Where two faces swap which is higher along their shared edge, the edge is split at
  the crossing, so each part has one wall on one side.
* At a vertex where several faces meet at different heights, a wall's vertical edge
  includes every other vertex at that position in between, so that no vertical edge
  is a T-junction.
* Heights within ``merge_tol_m`` at one position are one vertex.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import numpy.typing as npt

from mtl_roofs.geometry.roof import FaceMesh, RoofFace, plane_height

FloatArray = npt.NDArray[np.float64]


class SurfaceKind(Enum):
    """Semantic class of a solid's surface, as CityGML names them."""

    ROOF = "RoofSurface"
    WALL = "WallSurface"
    GROUND = "GroundSurface"


class ExtrusionStatus(Enum):
    """Outcome of extrusion. Values match the failure log statuses."""

    OK = "ok"
    NOT_WATERTIGHT = "not-watertight"


@dataclass(frozen=True, slots=True)
class Surface:
    """One planar surface of a solid.

    Attributes:
        kind: Roof, wall or ground.
        rings: Vertex indices; exterior first, then holes. The closing vertex is not
            repeated.
        face: For a roof surface, the index of its face in the partition.
    """

    kind: SurfaceKind
    rings: list[list[int]]
    face: int | None = None


@dataclass(frozen=True, slots=True)
class Solid:
    """A closed polyhedral solid in EPSG:2950, metres, CGVD28 heights."""

    vertices: FloatArray
    surfaces: list[Surface]

    def volume(self) -> float:
        """Signed volume; positive when the surfaces face outward."""
        # Relative to the centroid: at EPSG:2950 magnitudes the triple products
        # would lose most of their digits to cancellation.
        local = self.vertices - self.vertices.mean(axis=0)
        total = 0.0
        for surface in self.surfaces:
            for ring in surface.rings:
                pts = local[ring]
                for i in range(1, len(pts) - 1):
                    total += float(np.dot(pts[0], np.cross(pts[i], pts[i + 1])))
        return total / 6.0

    def rings(self) -> list[list[int]]:
        """Every ring of every surface, for the topological checks."""
        return [ring for surface in self.surfaces for ring in surface.rings]


@dataclass(frozen=True, slots=True)
class ExtrusionParams:
    """Extrusion and validity parameters.

    Attributes:
        merge_tol_m: Heights closer than this at one position are one vertex.
        planarity_tol_m: Largest distance of a vertex from its surface's plane.
        min_wall_m: Lowest the roof may come to the ground anywhere on the outline.
    """

    merge_tol_m: float = 1e-4
    planarity_tol_m: float = 1e-3
    min_wall_m: float = 0.5


@dataclass(frozen=True, slots=True)
class Extrusion:
    """A solid and whether it passed the validity checks.

    Attributes:
        solid: The solid; present even when invalid, so it can be inspected.
        status: ``ok`` or ``not-watertight``.
        problems: Why it is ``not-watertight``; empty when ``ok``.
    """

    solid: Solid
    status: ExtrusionStatus = ExtrusionStatus.OK
    problems: list[str] = field(default_factory=list)


def extrude_lod2(
    faces: list[RoofFace], ground_z: float, params: ExtrusionParams | None = None
) -> Extrusion:
    """Build a closed LOD2 solid from a roof partition and a ground height.

    Args:
        faces: The partition of one footprint, from
            :func:`~mtl_roofs.geometry.roof.partition_roof`.
        ground_z: Height of the floor, in metres above CGVD28.
        params: Defaults to :class:`ExtrusionParams`.

    Returns:
        The solid with its validity. It is ``not-watertight`` if it fails the
        edge-parity or orientation check, has a non-planar surface or a
        non-positive volume, or if the roof comes within ``min_wall_m`` of the ground.

    Raises:
        ValueError: if there are no faces.
    """
    p = params or ExtrusionParams()
    if not faces:
        msg = "cannot extrude a roof with no faces"
        raise ValueError(msg)
    mesh = FaceMesh.from_faces(faces)
    heights = _Heights(mesh, faces)
    _split_crossings(mesh, heights, p.merge_tol_m)
    builder = _Builder(mesh, heights, ground_z, p.merge_tol_m)
    solid = builder.build()
    problems = _validate(solid, faces, ground_z, p)
    status = ExtrusionStatus.NOT_WATERTIGHT if problems else ExtrusionStatus.OK
    return Extrusion(solid, status, problems)


# ------------------------------------------------------------------ building
@dataclass(slots=True)
class _Heights:
    """Roof height of each face at each of its nodes."""

    mesh: FaceMesh
    faces: list[RoofFace]

    def at(self, face: int, node: int) -> float:
        x, y = self.mesh.xy[node]
        return float(plane_height(self.faces[face].plane, np.array(x), np.array(y)))


def _split_crossings(mesh: FaceMesh, heights: _Heights, tol: float) -> None:
    """Insert a node wherever two faces swap which is higher along a shared edge."""
    edges = mesh.directed_edges()
    for (a, b), f in list(edges.items()):
        g = edges.get((b, a))
        if g is None or a > b:
            continue
        da = heights.at(f, a) - heights.at(g, a)
        db = heights.at(f, b) - heights.at(g, b)
        if abs(da) <= tol or abs(db) <= tol or da * db > 0:
            continue
        t = da / (da - db)
        node = len(mesh.xy)
        mesh.xy = np.vstack([mesh.xy, mesh.xy[a] + t * (mesh.xy[b] - mesh.xy[a])])
        _insert_between(mesh.rings[f], a, b, node)
        _insert_between(mesh.rings[g], b, a, node)


def _insert_between(rings: list[list[int]], a: int, b: int, node: int) -> None:
    for ring in rings:
        for i, n in enumerate(ring):
            if n == a and ring[(i + 1) % len(ring)] == b:
                ring.insert(i + 1, node)
                return


class _Builder:
    """Assembles the solid's shared vertices and surfaces."""

    def __init__(self, mesh: FaceMesh, heights: _Heights, ground_z: float, tol: float) -> None:
        self.mesh = mesh
        self.heights = heights
        self.ground_z = ground_z
        self.tol = tol
        self.vertices: list[tuple[float, float, float]] = []
        # Per node, the (height, vertex) pairs at that position, sorted by height.
        self.columns: dict[int, list[tuple[float, int]]] = {}

    def vertex(self, node: int, z: float) -> int:
        column = self.columns.setdefault(node, [])
        for height, vid in column:
            if abs(height - z) <= self.tol:
                return vid
        vid = len(self.vertices)
        x, y = self.mesh.xy[node]
        self.vertices.append((float(x), float(y), z))
        column.append((z, vid))
        column.sort()
        return vid

    def between(self, node: int, low: float, high: float) -> list[int]:
        """Vertices at a node strictly between two heights, upwards."""
        return [
            vid for z, vid in self.columns.get(node, []) if low + self.tol < z < high - self.tol
        ]

    def build(self) -> Solid:
        mesh, edges = self.mesh, self.mesh.directed_edges()
        roof_z = {
            (f, n): self.heights.at(f, n)
            for f, rings in enumerate(mesh.rings)
            for ring in rings
            for n in ring
        }
        boundary = [(a, b, f) for (a, b), f in edges.items() if (b, a) not in edges]

        # Every vertex first, so that walls can find the ones between their corners.
        for (_, n), z in roof_z.items():
            self.vertex(n, z)
        for a, _, _ in boundary:
            self.vertex(a, self.ground_z)

        surfaces = [
            Surface(SurfaceKind.ROOF, [[self.vertex(n, roof_z[f, n]) for n in r] for r in rings], f)
            for f, rings in enumerate(mesh.rings)
        ]
        for a, b, f in boundary:
            low = {a: self.ground_z, b: self.ground_z}
            high = {a: roof_z[f, a], b: roof_z[f, b]}
            self._wall(surfaces, a, b, low, high)
        for (a, b), f in edges.items():
            g = edges.get((b, a))
            if g is None or a > b:
                continue
            za = (roof_z[f, a], roof_z[g, a])
            zb = (roof_z[f, b], roof_z[g, b])
            if za[0] + zb[0] >= za[1] + zb[1]:  # f is the higher side: wall faces g
                self._wall(surfaces, a, b, {a: za[1], b: zb[1]}, {a: za[0], b: zb[0]})
            else:
                self._wall(surfaces, b, a, {a: za[0], b: zb[0]}, {a: za[1], b: zb[1]})
        surfaces.append(Surface(SurfaceKind.GROUND, self._floor(boundary)))
        return Solid(np.asarray(self.vertices, dtype=np.float64), surfaces)

    def _wall(
        self,
        surfaces: list[Surface],
        a: int,
        b: int,
        low: dict[int, float],
        high: dict[int, float],
    ) -> None:
        """The vertical wall under edge ``a → b``, facing right of that direction."""
        ring = [
            self.vertex(a, low[a]),
            self.vertex(b, low[b]),
            *self.between(b, low[b], high[b]),
            self.vertex(b, high[b]),
            self.vertex(a, high[a]),
            *reversed(self.between(a, low[a], high[a])),
        ]
        ring = [v for i, v in enumerate(ring) if v != ring[i - 1]]
        if len(ring) >= 3:
            surfaces.append(Surface(SurfaceKind.WALL, [ring]))

    def _floor(self, boundary: list[tuple[int, int, int]]) -> list[list[int]]:
        """The footprint at ground height, seen from below: the outline reversed."""
        successor = {a: b for a, b, _ in boundary}
        rings: list[list[int]] = []
        unvisited = set(successor)
        while unvisited:
            start = min(unvisited)
            ring = [start]
            unvisited.discard(start)
            # A partition that is not properly noded leaves an outline that does not
            # close; stop rather than loop, and let the edge-parity check report it.
            while (nxt := successor.get(ring[-1], start)) != start and nxt in unvisited:
                ring.append(nxt)
                unvisited.discard(nxt)
            rings.append([self.vertex(n, self.ground_z) for n in reversed(ring)])
        # Exterior first: seen from above the reversed exterior is clockwise, so it is
        # the ring of most negative plan area.
        rings.sort(key=lambda r: _plan_area(np.asarray(self.vertices)[r]))
        return rings


def _plan_area(pts: FloatArray) -> float:
    x, y = pts[:, 0], pts[:, 1]
    return float(0.5 * (np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


# ------------------------------------------------------------------ validity
def _validate(
    solid: Solid, faces: list[RoofFace], ground_z: float, p: ExtrusionParams
) -> list[str]:
    problems: list[str] = []
    rings = solid.rings()
    if not is_watertight(rings):
        problems.append("an edge is not shared by exactly two surfaces")
    elif not is_consistently_oriented(rings):
        problems.append("surfaces are not consistently oriented")
    for surface in solid.surfaces:
        pts = solid.vertices[[v for ring in surface.rings for v in ring]]
        if len(pts) > 3 and _planarity(pts) > p.planarity_tol_m:
            problems.append(f"a {surface.kind.value} is not planar")
            break
    roof = [v for s in solid.surfaces if s.kind is SurfaceKind.ROOF for r in s.rings for v in r]
    lowest = float(solid.vertices[roof, 2].min())
    if lowest < ground_z + p.min_wall_m:
        problems.append(f"the roof comes to {lowest - ground_z:.2f} m above the ground")
    if not problems and solid.volume() <= 0:
        problems.append("the solid has a non-positive volume")
    return problems


def _planarity(pts: FloatArray) -> float:
    """Largest distance of the points from their total-least-squares plane."""
    centred = pts - pts.mean(axis=0)
    normal = np.linalg.svd(centred, full_matrices=False)[2][2]
    return float(np.abs(centred @ normal).max())


def is_watertight(faces: list[list[int]]) -> bool:
    """Whether a face set is closed: every undirected edge used exactly twice.

    This is the cheap topological half of the validity check, independent of any
    geometry library, and it is what the extrusion tests assert against.

    Args:
        faces: Each face as a list of vertex indices, not repeating the first vertex.
    """
    edges: dict[tuple[int, int], int] = {}
    for face in faces:
        if len(face) < 3:
            return False
        for i, start in enumerate(face):
            end = face[(i + 1) % len(face)]
            key = (start, end) if start < end else (end, start)
            edges[key] = edges.get(key, 0) + 1
    return bool(edges) and all(count == 2 for count in edges.values())


def is_consistently_oriented(faces: list[list[int]]) -> bool:
    """Whether every directed edge is traversed once each way.

    On a closed surface this means neighbouring faces agree on which side is out; with
    a positive volume, every face then faces outward.
    """
    directed: set[tuple[int, int]] = set()
    for face in faces:
        for i, start in enumerate(face):
            edge = (start, face[(i + 1) % len(face)])
            if edge in directed:
                return False
            directed.add(edge)
    return all((b, a) in directed for a, b in directed)
