"""Partition of a footprint into roof faces, one solved plane per face.

The topology solver returns planes and their inliers, not faces. This module decides
which plane roofs each part of the footprint, and where the boundaries between them
run. Extrusion (:mod:`mtl_roofs.geometry.extrude`) and the evaluation's height grid
both read the same partition, so the surface that is scored is the surface that is
exported.

The partition is built in four steps:

1. **Label a grid.** Every ``cell_m`` cell touching the footprint is given to the plane
   that best explains the LiDAR there: among the planes with an inlier within
   ``candidate_radius_m``, the one whose height is closest to the local median point
   height. With no points nearby, the plane with the nearest inlier wins.
2. **Clean it.** A majority filter removes isolated cells, and regions smaller than
   ``min_face_area_m2`` are given to the neighbour they share most cells with.
3. **Vectorise it.** Each connected region becomes a polygon, clipped exactly to the
   footprint, and the region boundaries are simplified as one coverage, so that
   neighbouring faces keep sharing identical edges.
4. **Snap boundaries to intersections.** Where two non-parallel planes meet, the
   boundary between their faces should lie on the line along which they intersect,
   so that the roof is continuous across it. Each boundary vertex within ``snap_m``
   of that line is moved onto it. A vertex where three or more planes meet goes to
   the point closest to all their intersection lines. A move that would invalidate
   a face is not made.

Boundaries left between near-parallel planes (``step_angle_deg``) are not snapped:
the faces are at different heights and the extrusion joins them with a vertical wall.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations, pairwise

import numpy as np
import numpy.typing as npt
import shapely
from scipy import ndimage
from scipy.spatial import cKDTree

from mtl_roofs.geometry.planes import Plane

FloatArray = npt.NDArray[np.float64]
IndexArray = npt.NDArray[np.intp]

#: Node coordinates are matched after rounding to this many decimals (0.1 µm).
_NODE_DECIMALS = 7


@dataclass(frozen=True, slots=True)
class RoofParams:
    """Roof partition parameters.

    Attributes:
        cell_m: Labelling grid cell size.
        height_radius_m: Roof points within this distance of a cell centre give its
            observed height.
        height_neighbours: At most this many of them, nearest first.
        candidate_radius_m: Planes with an inlier this close to a cell compete for it.
        smoothing_passes: Rounds of the 3 x 3 majority filter.
        min_face_area_m2: Smaller regions are merged into a neighbour.
        simplify_m: Coverage simplification tolerance for face boundaries.
        snap_m: Largest move of a boundary vertex onto a plane intersection.
        step_angle_deg: Planes closer than this to parallel are never snapped
            together; matches the topology solver's step threshold.
    """

    cell_m: float = 0.25
    height_radius_m: float = 0.75
    height_neighbours: int = 8
    candidate_radius_m: float = 2.0
    smoothing_passes: int = 2
    min_face_area_m2: float = 1.0
    simplify_m: float = 0.5
    snap_m: float = 1.0
    step_angle_deg: float = 10.0


@dataclass(frozen=True, slots=True)
class RoofFace:
    """One roof face: a 2D polygon carried by one plane.

    Attributes:
        plane_index: Index into the planes the partition was built from.
        plane: That plane.
        polygon: The face in plan, EPSG:2950. Exterior counter-clockwise, holes
            clockwise.
    """

    plane_index: int
    plane: Plane
    polygon: shapely.Polygon


def plane_height(plane: Plane, x: FloatArray, y: FloatArray) -> FloatArray:
    """Height of a non-vertical plane above each ``(x, y)``."""
    nx, ny, nz = plane.normal
    z: FloatArray = (plane.offset - nx * np.asarray(x) - ny * np.asarray(y)) / nz
    return z


def partition_roof(
    footprint: shapely.Polygon,
    points: FloatArray,
    planes: list[tuple[Plane, IndexArray]],
    params: RoofParams | None = None,
) -> list[RoofFace]:
    """Split a footprint into roof faces, one plane each.

    Args:
        footprint: The footprint polygon, in EPSG:2950.
        points: ``(n, 3)`` roof points, as given to plane detection.
        planes: ``(plane, inlier indices)``, as solved by the topology solver.
        params: Defaults to :class:`RoofParams`.

    Returns:
        Faces that exactly cover the footprint without overlap, neighbours sharing
        identical edges. Empty if there are no planes.
    """
    p = params or RoofParams()
    if not planes or footprint.is_empty:
        return []
    footprint = shapely.normalize(shapely.make_valid(footprint))
    if not isinstance(footprint, shapely.Polygon):
        msg = f"expected a polygon footprint, got {footprint.geom_type}"
        raise ValueError(msg)

    grid = _Grid.covering(footprint, p.cell_m)
    labels = _label_cells(grid, np.asarray(points, dtype=np.float64), planes, p)
    labels = _smooth(labels, grid.mask, len(planes), p.smoothing_passes)
    labels = _merge_small(labels, grid.mask, p.min_face_area_m2 / p.cell_m**2)
    polygons, owners = _vectorise(footprint, grid, labels)
    simplified = shapely.coverage_simplify(polygons, p.simplify_m, simplify_boundary=False)
    if _is_partition(footprint, simplified):
        polygons = simplified
    faces = [
        RoofFace(int(owner), planes[owner][0], shapely.Polygon(poly))
        for poly, owner in zip(polygons, owners, strict=True)
    ]
    return _snap(footprint, faces, p)


# ------------------------------------------------------------------ labelling
@dataclass(frozen=True, slots=True)
class _Grid:
    """A raster aligned on the footprint's bounds; row 0 is the southern row."""

    x0: float
    y0: float
    cell: float
    shape: tuple[int, int]
    mask: npt.NDArray[np.bool_]

    @classmethod
    def covering(cls, footprint: shapely.Polygon, cell: float) -> _Grid:
        xmin, ymin, xmax, ymax = footprint.bounds
        x0, y0 = xmin - cell, ymin - cell
        nx = int(np.ceil((xmax - x0) / cell)) + 1
        ny = int(np.ceil((ymax - y0) / cell)) + 1
        boxes = shapely.box(
            x0 + cell * np.arange(nx)[None, :],
            y0 + cell * np.arange(ny)[:, None],
            x0 + cell * (np.arange(nx)[None, :] + 1),
            y0 + cell * (np.arange(ny)[:, None] + 1),
        )
        shapely.prepare(footprint)
        mask = shapely.intersects(footprint, boxes) & ~shapely.touches(footprint, boxes)
        return cls(x0, y0, cell, (ny, nx), np.asarray(mask, dtype=bool))

    def centres(self) -> tuple[FloatArray, FloatArray]:
        rows, cols = np.nonzero(self.mask)
        x = (self.x0 + self.cell * (cols + 0.5)).astype(np.float64)
        y = (self.y0 + self.cell * (rows + 0.5)).astype(np.float64)
        return x, y

    def cell_of(self, x: float, y: float) -> tuple[int, int]:
        return int((y - self.y0) // self.cell), int((x - self.x0) // self.cell)


def _label_cells(
    grid: _Grid, points: FloatArray, planes: list[tuple[Plane, IndexArray]], p: RoofParams
) -> npt.NDArray[np.int32]:
    """Give each footprint cell to the plane that best explains the points there."""
    x, y = grid.centres()
    xy = np.column_stack([x, y])

    # Distance from each cell to each plane's nearest inlier.
    near = np.full((len(planes), len(x)), np.inf)
    for i, (_, idx) in enumerate(planes):
        if len(idx):
            near[i] = cKDTree(points[idx, :2]).query(xy)[0]

    # Observed height: median of the nearest roof points within the radius.
    k = min(p.height_neighbours, len(points))
    dist, nbr = cKDTree(points[:, :2]).query(xy, k=k, distance_upper_bound=p.height_radius_m)
    dist, nbr = np.asarray(dist).reshape(len(x), k), np.asarray(nbr).reshape(len(x), k)
    found = np.isfinite(dist)
    heights = np.where(found, points[np.minimum(nbr, len(points) - 1), 2], np.nan)
    observed = np.full(len(x), np.nan)
    has_obs = found.any(axis=1)
    observed[has_obs] = np.nanmedian(heights[has_obs], axis=1)

    predicted = np.stack([plane_height(plane, x, y) for plane, _ in planes])
    misfit = np.abs(predicted - observed[None, :])
    misfit[near > p.candidate_radius_m] = np.inf
    by_height = np.argmin(misfit, axis=0)
    by_distance = np.argmin(near, axis=0)
    decided = np.isfinite(misfit[by_height, np.arange(len(x))])

    labels = np.full(grid.shape, -1, dtype=np.int32)
    labels[grid.mask] = np.where(decided, by_height, by_distance)
    return labels


def _smooth(
    labels: npt.NDArray[np.int32], mask: npt.NDArray[np.bool_], n_labels: int, passes: int
) -> npt.NDArray[np.int32]:
    """3 x 3 majority filter inside the mask; a cell keeps its label on a tie."""
    out = labels.copy()
    for _ in range(passes):
        votes = np.stack(
            [
                ndimage.uniform_filter((out == i).astype(np.float64), size=3, mode="constant")
                for i in range(n_labels)
            ]
        )
        winner = np.argmax(votes, axis=0)
        best = np.take_along_axis(votes, winner[None], axis=0)[0]
        own = np.take_along_axis(votes, np.clip(out, 0, None)[None], axis=0)[0]
        change = mask & (best > own + 1e-9)
        out = np.where(change, winner, out).astype(np.int32)
    return out


def _merge_small(
    labels: npt.NDArray[np.int32], mask: npt.NDArray[np.bool_], min_cells: float
) -> npt.NDArray[np.int32]:
    """Give each region smaller than ``min_cells`` to its most-bordering neighbour."""
    out = labels.copy()
    cross = ndimage.generate_binary_structure(2, 1)
    for _ in range(50):
        regions = _regions(out, mask)
        count = int(regions.max())
        sizes = np.bincount(regions.ravel(), minlength=count + 1)
        small = [r for r in np.argsort(sizes) if r > 0 and sizes[r] < min_cells]
        if not small:
            break
        changed = False
        for r in small:
            region = regions == r
            ring = ndimage.binary_dilation(region, structure=cross) & ~region & mask
            neighbours = out[ring]
            neighbours = neighbours[neighbours != out[region][0]]
            if len(neighbours):
                out[region] = np.bincount(neighbours).argmax()
                changed = True
        if not changed:
            break
    return out


def _regions(labels: npt.NDArray[np.int32], mask: npt.NDArray[np.bool_]) -> npt.NDArray[np.intp]:
    """Number the 4-connected regions of equal label inside the mask, from 1."""
    regions = np.zeros(labels.shape, dtype=np.intp)
    offset = 0
    for value in np.unique(labels[mask]):
        part, n = ndimage.label(mask & (labels == value))
        regions[part > 0] = part[part > 0] + offset
        offset += n
    return regions


def _vectorise(
    footprint: shapely.Polygon, grid: _Grid, labels: npt.NDArray[np.int32]
) -> tuple[npt.NDArray[np.object_], list[int]]:
    """Polygons of the labelled regions, clipped to the footprint as one noded coverage.

    Clipping each region separately would compute the same boundary crossing twice,
    possibly to different last bits, and the faces would no longer share edges. So
    the region boundaries and the footprint outline are noded together once, and the
    resulting pieces are dissolved back into regions.
    """
    regions = _regions(labels, grid.mask)
    edges = _region_edges(regions, grid)
    lines = shapely.union_all([footprint.boundary, edges])
    pieces = shapely.get_parts(shapely.polygonize(shapely.get_parts(lines)))
    pieces = pieces[shapely.area(pieces) > 1e-9]
    inside = shapely.contains_properly(footprint, shapely.point_on_surface(pieces))
    pieces = pieces[inside]

    by_region: dict[int, list[shapely.Polygon]] = {}
    for piece in pieces:
        point = shapely.point_on_surface(piece)
        row, col = grid.cell_of(point.x, point.y)
        by_region.setdefault(int(regions[row, col]), []).append(piece)

    polygons: list[shapely.Polygon] = []
    owners: list[int] = []
    for region, parts in sorted(by_region.items()):
        row, col = np.argwhere(regions == region)[0]
        merged = shapely.coverage_union_all(parts)
        for part in shapely.get_parts(merged):
            polygons.append(shapely.normalize(part))
            owners.append(int(labels[row, col]))
    return np.array(polygons, dtype=object), owners


def _region_edges(regions: npt.NDArray[np.intp], grid: _Grid) -> shapely.MultiLineString:
    """Every cell edge separating two different regions (or a region from outside)."""
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    ny, nx = regions.shape
    x = grid.x0 + grid.cell * np.arange(nx + 1)
    y = grid.y0 + grid.cell * np.arange(ny + 1)
    vertical = np.argwhere(regions[:, 1:] != regions[:, :-1])
    segments += [((x[c + 1], y[r]), (x[c + 1], y[r + 1])) for r, c in vertical]
    horizontal = np.argwhere(regions[1:, :] != regions[:-1, :])
    segments += [((x[c], y[r + 1]), (x[c + 1], y[r + 1])) for r, c in horizontal]
    return shapely.line_merge(shapely.MultiLineString(segments))


def _is_partition(footprint: shapely.Polygon, polygons: npt.NDArray[np.object_]) -> bool:
    """Whether the polygons are valid, non-empty and tile the footprint exactly."""
    if not all(isinstance(g, shapely.Polygon) and g.is_valid and g.area > 0 for g in polygons):
        return False
    if not bool(shapely.coverage_is_valid(polygons)):
        return False
    return bool(abs(sum(g.area for g in polygons) - footprint.area) < 1e-6 * footprint.area)


# ------------------------------------------------------------------ snapping
def _snap(footprint: shapely.Polygon, faces: list[RoofFace], p: RoofParams) -> list[RoofFace]:
    """Move boundary vertices onto the lines along which their planes intersect.

    The vertices strictly inside one boundary between two faces move together or not
    at all: if the intersection line is out of reach of any of them, it is not where
    that boundary runs, and moving only the nearer vertices would leave a zigzag.
    """
    mesh = FaceMesh.from_faces(faces)
    outline = shapely.get_parts(shapely.get_rings(footprint))
    corners = {i for i, xy in enumerate(mesh.xy) if _key(xy) in _outline_keys(outline)}
    boundary = mesh.boundary_nodes()
    incident = mesh.node_faces()

    targets: dict[int, FloatArray | None] = {}
    chains: dict[frozenset[int], list[int]] = {}
    for node, around in enumerate(incident):
        if node in corners:
            continue
        planes = {faces[f].plane_index: faces[f].plane for f in around}
        target = _intersection_point(
            mesh.xy[node], list(planes.values()), node in boundary, outline, p
        )
        if target is not None and np.hypot(*(target - mesh.xy[node])) > p.snap_m:
            target = None
        targets[node] = target
        if len(around) == 2 and node not in boundary:
            chains.setdefault(frozenset(around), []).append(node)
    for members in chains.values():
        if any(targets[n] is None for n in members):
            for n in members:
                targets[n] = None

    moved = False
    for node, target in targets.items():
        if target is None:
            continue
        before = mesh.xy[node].copy()
        mesh.xy[node] = target
        if all(mesh.polygon(f).is_valid for f in incident[node]):
            moved = True
        else:
            mesh.xy[node] = before
    mesh.merge_coincident()
    moved |= mesh.drop_collinear(corners)
    if not moved:
        return faces
    if any(len(ring) < 3 for rings in mesh.rings for ring in rings):
        return faces
    snapped = [
        RoofFace(face.plane_index, face.plane, mesh.polygon(i)) for i, face in enumerate(faces)
    ]
    polygons = np.array([f.polygon for f in snapped], dtype=object)
    return snapped if _is_partition(footprint, polygons) else faces


def _intersection_point(
    xy: FloatArray,
    planes: list[Plane],
    on_boundary: bool,
    outline: npt.NDArray[np.object_],
    p: RoofParams,
) -> FloatArray | None:
    """Where a boundary vertex should go so that its planes agree in height there."""
    lines = []
    for a, b in combinations(planes, 2):
        if a.angle_to(b) < p.step_angle_deg:
            continue
        g, h = _height_line(a, b)
        lines.append((g, h))
    if not lines:
        return None
    if on_boundary:
        if len(lines) != 1:
            return None
        return _along_outline(xy, *lines[0], outline)
    if len(lines) == 1:
        g, h = lines[0]
        target: FloatArray = xy - (g @ xy - h) * g
        return target
    normals = np.array([g for g, _ in lines])
    rhs = np.array([h for _, h in lines])
    if np.linalg.cond(normals) > 1e3:
        return None
    solved: FloatArray = np.linalg.lstsq(normals, rhs, rcond=None)[0]
    return solved


def _height_line(a: Plane, b: Plane) -> tuple[FloatArray, float]:
    """The 2D line ``g · xy = h``, ``|g| = 1``, along which two planes are equally high."""
    na, nb = np.asarray(a.normal), np.asarray(b.normal)
    g = nb[:2] / nb[2] - na[:2] / na[2]
    h = b.offset / nb[2] - a.offset / na[2]
    norm = float(np.linalg.norm(g))
    return g / norm, h / norm


def _along_outline(
    xy: FloatArray, g: FloatArray, h: float, outline: npt.NDArray[np.object_]
) -> FloatArray | None:
    """Where a height line crosses the footprint edge an outline vertex lies on."""
    for ring in outline:
        coords = np.asarray(ring.coords)
        for start, end in pairwise(coords):
            seg = end - start
            length = float(np.linalg.norm(seg))
            if length == 0.0:
                continue
            rel = xy - start
            offset = abs(float(seg[0] * rel[1] - seg[1] * rel[0])) / length
            if offset > 1e-6:
                continue
            denom = float(g @ seg)
            if abs(denom) < 1e-12:
                return None
            t = (h - float(g @ start)) / denom
            # A crossing exactly at the edge's end is the footprint corner itself; the
            # vertex moves onto it and is merged with it.
            if not -1e-9 <= t <= 1.0 + 1e-9:
                return None
            target: FloatArray = start + min(max(t, 0.0), 1.0) * seg
            return target
    return None


def _outline_keys(outline: npt.NDArray[np.object_]) -> set[tuple[float, float]]:
    return {_key(c) for ring in outline for c in ring.coords}


def _key(xy: tuple[float, float] | FloatArray) -> tuple[float, float]:
    return round(float(xy[0]), _NODE_DECIMALS), round(float(xy[1]), _NODE_DECIMALS)


# ------------------------------------------------------------------ shared nodes
@dataclass(slots=True)
class FaceMesh:
    """Faces as rings of shared 2D nodes: the planar graph of a partition.

    Attributes:
        xy: ``(n, 2)`` node coordinates.
        rings: Per face, its rings as node lists: exterior first and counter-clockwise,
            holes clockwise, the closing node not repeated.
    """

    xy: FloatArray
    rings: list[list[list[int]]]

    @classmethod
    def from_faces(cls, faces: list[RoofFace]) -> FaceMesh:
        """Index a partition's vertices; equal coordinates become one node."""
        index: dict[tuple[float, float], int] = {}
        coords: list[tuple[float, float]] = []
        rings: list[list[list[int]]] = []

        def node(c: tuple[float, float]) -> int:
            key = _key(c)
            if key not in index:
                index[key] = len(coords)
                coords.append((float(c[0]), float(c[1])))
            return index[key]

        for face in faces:
            poly = shapely.orient_polygons(face.polygon)
            face_rings = []
            for ring in [poly.exterior, *poly.interiors]:
                ids = [node(c) for c in ring.coords[:-1]]
                ids = [n for i, n in enumerate(ids) if n != ids[i - 1]]
                face_rings.append(ids)
            rings.append(face_rings)
        return cls(np.asarray(coords, dtype=np.float64).reshape(-1, 2), rings)

    def polygon(self, face: int) -> shapely.Polygon:
        """A face as a polygon, at the nodes' current positions."""
        exterior, *holes = self.rings[face]
        return shapely.Polygon(self.xy[exterior], [self.xy[h] for h in holes])

    def node_faces(self) -> list[list[int]]:
        """Per node, the faces whose rings hold it."""
        incident: list[list[int]] = [[] for _ in range(len(self.xy))]
        for f, rings in enumerate(self.rings):
            for n in {n for ring in rings for n in ring}:
                incident[n].append(f)
        return incident

    def directed_edges(self) -> dict[tuple[int, int], int]:
        """Each directed edge ``(a, b)`` of every ring, mapped to its face."""
        edges: dict[tuple[int, int], int] = {}
        for f, rings in enumerate(self.rings):
            for ring in rings:
                for i, a in enumerate(ring):
                    edges[(a, ring[(i + 1) % len(ring)])] = f
        return edges

    def merge_coincident(self) -> None:
        """Make nodes moved onto the same position one node, and drop repeats in rings."""
        first: dict[tuple[float, float], int] = {}
        alias = {n: first.setdefault(_key(xy), n) for n, xy in enumerate(self.xy)}
        for rings in self.rings:
            for r, ring in enumerate(rings):
                ids = [alias[n] for n in ring]
                rings[r] = [n for i, n in enumerate(ids) if n != ids[i - 1]]

    def drop_collinear(self, keep: set[int], tol: float = 1e-6) -> bool:
        """Remove nodes lying on the straight line between their neighbours in every ring.

        Only nodes appearing once in each ring that holds them are candidates, so
        that both faces either side of a boundary lose the node together.

        Returns:
            Whether any node was removed.
        """
        holders: dict[int, list[tuple[int, int]]] = {}
        for f, rings in enumerate(self.rings):
            for r, ring in enumerate(rings):
                for n in ring:
                    holders.setdefault(n, []).append((f, r))
        removed = False
        for node, where in holders.items():
            if node in keep or len(set(where)) != len(where):
                continue
            if all(
                len(self.rings[f][r]) > 3 and self._collinear(self.rings[f][r], node, tol)
                for f, r in where
            ):
                for f, r in where:
                    self.rings[f][r].remove(node)
                removed = True
        return removed

    def _collinear(self, ring: list[int], node: int, tol: float) -> bool:
        i = ring.index(node)
        a, m, b = self.xy[ring[i - 1]], self.xy[node], self.xy[ring[(i + 1) % len(ring)]]
        seg = b - a
        length = float(np.hypot(*seg))
        if length == 0.0:
            return False
        rel = m - a
        offset = abs(float(seg[0] * rel[1] - seg[1] * rel[0])) / length
        along = float(seg @ rel) / length**2
        return offset < tol and 0.0 < along < 1.0

    def boundary_nodes(self) -> set[int]:
        """Nodes on the footprint outline: on an edge with no twin."""
        edges = self.directed_edges()
        return {n for (a, b) in edges if (b, a) not in edges for n in (a, b)}
