"""A static scene for the v0 viewer: one JSON index plus one binary of typed arrays.

This is deliberately **not** 3D Tiles (ADR 0002 is still open). A v0 study area is about
a square kilometre, small enough to ship whole, so the viewer loads two files and
needs no tiling, streaming or LOD (ADR 0003).

``scene.bin`` is a concatenation of little-endian typed arrays; ``scene.json`` lists
each one under ``arrays`` with its dtype, byte offset and length, so the viewer can
view the buffer without parsing it. Positions are quantised to ``uint16`` on a grid of
``scale`` metres from ``origin`` (EPSG:2950 / CGVD28), which is a few centimetres for a
1.5 km area: finer than the LiDAR spacing and far finer than anything on screen.

Arrays:

``rec_pos`` / ``rec_kind`` / ``rec_owner``
    Our reconstructions as a triangle soup: 9 coordinates per triangle, its surface
    kind (:data:`KIND_CODES`) and the index of its footprint in ``footprints``.
``pad_pos`` / ``pad_owner``
    Footprints that did not reconstruct, as flat pads at their ground height, so a
    failure is visible rather than a gap.
``ref_pos`` / ``ref_owner``
    The reference model's roof surfaces, owner indexing ``references``.
``pts_pos`` / ``pts_class``
    A random sample of the LiDAR around the area, ASPRS class per point.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import shapely

from mtl_roofs.evaluation.evaluate import summarise
from mtl_roofs.evaluation.report import BuildingResult
from mtl_roofs.io.lidar import read_points

FloatArray = npt.NDArray[np.float64]

#: Surface kinds as stored in ``reconstructions.surface_kinds``, and their codes.
KIND_CODES = {"RoofSurface": 0, "WallSurface": 1, "GroundSurface": 2}
#: LiDAR classes worth drawing: ground, the three vegetation classes, building, water.
POINT_CLASSES = (2, 3, 4, 5, 6, 9)
#: Points kept per class, so that roofs are not drowned by ground returns.
POINT_QUOTA = {2: 350_000, 3: 20_000, 4: 60_000, 5: 270_000, 6: 800_000, 9: 5_000}
#: Margin kept around the reconstructed area, metres.
MARGIN_M = 25.0
#: Height of a failure pad above its ground, metres; keeps it off the ground points.
PAD_LIFT_M = 0.3


def triangulate(rings: Sequence[FloatArray]) -> FloatArray:
    """Triangles of one planar 3D polygon, as ``(n, 3, 3)``.

    The polygon is projected onto the coordinate plane its normal is most aligned with,
    triangulated there with a constrained Delaunay triangulation, and lifted back. That
    triangulation adds no vertices, so every triangle corner is an input vertex and
    keeps its exact 3D position.

    Args:
        rings: The exterior ring, then any holes, each ``(m, 3)`` with or without the
            closing vertex.
    """
    rings = [r[:-1] if len(r) > 1 and np.array_equal(r[0], r[-1]) else r for r in rings]
    shell = rings[0]
    if len(shell) < 3:
        return np.empty((0, 3, 3))
    if len(rings) == 1 and len(shell) == 3:
        return shell[None, :, :].astype(float)
    # least-squares plane rather than Newell's normal: a self-intersecting ring's Newell
    # normal can cancel to zero, its best-fit plane cannot
    centre = shell.mean(axis=0)
    normal = np.linalg.svd(shell - centre)[2][-1]
    drop = int(np.argmax(np.abs(normal)))
    keep = [i for i in range(3) if i != drop]
    lift = {tuple(np.round(p[keep], 6)): p for ring in rings for p in ring}

    def on_plane(uv: FloatArray) -> FloatArray:
        # a vertex that repairing an invalid polygon introduced: put it on the plane
        p = np.empty(3)
        p[keep] = uv
        p[drop] = centre[drop] - normal[keep] @ (uv - centre[keep]) / normal[drop]
        return p

    polygon = shapely.Polygon(shell[:, keep], [h[:, keep] for h in rings[1:] if len(h) >= 3])
    if not polygon.is_valid:
        polygon = shapely.make_valid(polygon)
    out = []
    for tri in shapely.get_parts(shapely.constrained_delaunay_triangles(polygon)):
        corners = np.asarray(tri.exterior.coords)[:3]
        found = [lift.get(tuple(np.round(c, 6))) for c in corners]
        out.append([on_plane(c) if p is None else p for p, c in zip(found, corners, strict=True)])
    return np.array(out, dtype=float) if out else np.empty((0, 3, 3))


@dataclass(frozen=True, slots=True)
class Quantiser:
    """Maps EPSG:2950 coordinates onto a ``uint16`` grid and back."""

    origin: tuple[float, float, float]
    scale: float

    @classmethod
    def covering(cls, lo: FloatArray, hi: FloatArray) -> Quantiser:
        """The finest grid that holds the box from ``lo`` to ``hi`` in 65,535 steps."""
        extent = float(np.max(hi - lo))
        scale = max(extent / 65_000.0, 1e-3)
        return cls((float(lo[0]), float(lo[1]), float(lo[2])), scale)

    def encode(self, xyz: FloatArray) -> npt.NDArray[np.uint16]:
        """Quantise ``(..., 3)`` coordinates; raises if any falls outside the grid."""
        q = np.rint((xyz - np.asarray(self.origin)) / self.scale)
        if q.size and (q.min() < 0 or q.max() > 65_535):
            msg = "coordinates fall outside the quantisation grid"
            raise ValueError(msg)
        out: npt.NDArray[np.uint16] = q.astype(np.uint16)
        return out

    def decode(self, q: npt.NDArray[np.uint16]) -> FloatArray:
        """Inverse of :meth:`encode`, to within half a grid step."""
        out: FloatArray = q.astype(float) * self.scale + np.asarray(self.origin)
        return out


@dataclass
class _Writer:
    """Appends typed arrays to one buffer, 4-byte aligned, and records where each is."""

    chunks: list[bytes] = field(default_factory=list)
    index: dict[str, dict[str, Any]] = field(default_factory=dict)
    offset: int = 0

    def add(self, name: str, array: npt.NDArray[Any]) -> None:
        data = np.ascontiguousarray(array).astype(array.dtype.newbyteorder("<")).tobytes()
        self.index[name] = {
            "dtype": array.dtype.name,
            "offset": self.offset,
            "length": int(array.size),
        }
        pad = (-len(data)) % 4
        self.chunks.append(data + b"\0" * pad)
        self.offset += len(data) + pad


def _rmse(rows: Iterable[BuildingResult]) -> float | None:
    """Pooled RMSE over rows, the way :func:`summarise` pools the whole area."""
    rows = list(rows)
    n = sum(r.n_valid_cells for r in rows)
    return float(np.sqrt(sum(r.sum_sq_m2 for r in rows) / n)) if n else None


def _num(v: float | None, digits: int = 3) -> float | None:
    return None if v is None or not np.isfinite(v) else round(float(v), digits)


def _building(r: BuildingResult, ref_index: dict[str, int]) -> dict[str, Any]:
    return {
        "id": r.building_id,
        "status": r.status,
        "roofType": r.roof_type,
        "rmse": _num(r.rmse_m),
        "coverage": _num(r.coverage),
        "orientation": _num(r.orientation_median_deg, 2),
        "predFaces": r.n_pred_faces,
        "refFaces": r.n_ref_faces,
        "temporalOffset": _num(r.temporal_offset_m),
        "ref": ref_index.get(r.building_id),
    }


def _sample_points(
    tiles: Sequence[Path], lo: FloatArray, hi: FloatArray, seed: int = 0
) -> tuple[FloatArray, npt.NDArray[np.uint8]]:
    """Up to :data:`POINT_QUOTA` points of each class inside the box, uniformly.

    Every point draws a random key and the smallest keys win, per class, across all
    tiles: a uniform sample without replacement that never holds a whole tile's worth
    of points per class, and does not favour a tile for having fewer points.
    """
    rng = np.random.default_rng(seed)
    kept: dict[int, list[tuple[FloatArray, FloatArray]]] = {c: [] for c in POINT_CLASSES}
    for tile in tiles:
        ps = read_points(tile)
        inside = np.all((ps.xyz[:, :2] >= lo[:2]) & (ps.xyz[:, :2] <= hi[:2]), axis=1)
        for c in POINT_CLASSES:
            pts = ps.xyz[inside & (ps.classification == c)]
            keys = rng.random(len(pts))
            best = np.argsort(keys)[: POINT_QUOTA[c]]
            kept[c].append((pts[best], keys[best]))
    xyz_parts, cls_parts = [], []
    for c in POINT_CLASSES:
        if not kept[c]:
            continue
        pts = np.concatenate([p for p, _ in kept[c]])
        keys = np.concatenate([k for _, k in kept[c]])
        best = np.argsort(keys)[: POINT_QUOTA[c]]
        xyz_parts.append(pts[best])
        cls_parts.append(np.full(len(best), c, dtype=np.uint8))
    return np.concatenate(xyz_parts), np.concatenate(cls_parts)


def build_scene(
    conn: Any, area: str, tiles: Sequence[Path], out: Path, *, seed: int = 0
) -> dict[str, Any]:
    """Write ``scene.json`` and ``scene.bin`` for an area's latest run; returns the index.

    Args:
        conn: An open PostGIS connection (:func:`mtl_roofs.io.postgis.connect`).
        area: The study area, as loaded and reconstructed.
        tiles: The area's LiDAR tiles.
        out: Directory to write into; created if missing.
        seed: Seed for the point sample, so a re-export is byte-identical.
    """
    from mtl_roofs.io.postgis import load_building_results

    results = load_building_results(conn, area)
    if not results:
        msg = f"no evaluation results for area {area!r}: run `mtl-roofs evaluate` first"
        raise ValueError(msg)
    recs = conn.execute(
        "SELECT r.footprint_id, r.status, r.reason, r.n_points, r.density, r.ground_z,"
        " r.run_id, r.surface_kinds, ST_AsBinary(f.geom) FROM reconstructions r"
        " JOIN footprints f USING (area, footprint_id) WHERE r.area = %s"
        " ORDER BY r.footprint_id",
        (area,),
    ).fetchall()
    fp_index = {row[0]: i for i, row in enumerate(recs)}

    # reconstruction surfaces, in solid order so that surface_kinds lines up
    surfaces: dict[str, list[tuple[int, list[FloatArray]]]] = {}
    for fid, path, wkb in conn.execute(
        "SELECT footprint_id, (d).path[1], ST_AsBinary((d).geom) FROM ("
        " SELECT footprint_id, ST_Dump(solid) AS d FROM reconstructions"
        " WHERE area = %s AND status = 'ok') s",
        (area,),
    ):
        poly = shapely.from_wkb(bytes(wkb))
        rings = [np.asarray(poly.exterior.coords), *(np.asarray(i.coords) for i in poly.interiors)]
        surfaces.setdefault(fid, []).append((path - 1, rings))

    refs = conn.execute(
        "SELECT gml_id, ST_AsBinary(geom) FROM reference_roof_surfaces WHERE area = %s"
        " ORDER BY gml_id, ordinal",
        (area,),
    ).fetchall()
    ref_ids = sorted({gid for gid, _ in refs})
    ref_index = {gid: i for i, gid in enumerate(ref_ids)}

    rec_tris, rec_kind, rec_owner = [], [], []
    pad_tris, pad_owner = [], []
    for i, (fid, status, _r, _n, _d, ground_z, _run, kinds, fp_wkb) in enumerate(recs):
        if status == "ok":
            for k, rings in surfaces.get(fid, []):
                kind = KIND_CODES[kinds[k]]
                if kind == KIND_CODES["GroundSurface"]:
                    continue  # never seen from above, and it would z-fight the points
                t = triangulate(rings)
                rec_tris.append(t)
                rec_kind.append(np.full(len(t), kind, dtype=np.uint8))
                rec_owner.append(np.full(len(t), i, dtype=np.uint16))
        else:
            fp = shapely.from_wkb(bytes(fp_wkb))
            for part in shapely.get_parts(fp):
                outline = [
                    np.asarray(part.exterior.coords),
                    *(np.asarray(r.coords) for r in part.interiors),
                ]
                t = triangulate(
                    [
                        np.column_stack([r[:, :2], np.full(len(r), ground_z + PAD_LIFT_M)])
                        for r in outline
                    ]
                )
                pad_tris.append(t)
                pad_owner.append(np.full(len(t), i, dtype=np.uint16))

    def cat(parts: list[FloatArray]) -> FloatArray:
        return np.concatenate(parts) if parts else np.empty((0, 3, 3))

    rec_all, pad_all = cat(rec_tris), cat(pad_tris)
    # the scene is the area's footprints; the city model and LiDAR reach further
    footprint_xyz = np.concatenate([a.reshape(-1, 3) for a in (rec_all, pad_all)])
    margin = np.array([MARGIN_M, MARGIN_M, 0.0])
    lo = footprint_xyz.min(axis=0) - margin
    hi = footprint_xyz.max(axis=0) + margin

    ref_tris, ref_owner = [], []
    for gid, wkb in refs:
        poly = shapely.from_wkb(bytes(wkb))
        t = triangulate(
            [np.asarray(poly.exterior.coords), *(np.asarray(r.coords) for r in poly.interiors)]
        )
        if len(t) and np.all((t[..., :2] >= lo[:2]) & (t[..., :2] <= hi[:2])):
            ref_tris.append(t)
            ref_owner.append(np.full(len(t), ref_index[gid], dtype=np.uint16))
    ref_all = cat(ref_tris)
    lo[2] = min(lo[2], float(ref_all[..., 2].min())) if len(ref_all) else lo[2]
    hi[2] = max(hi[2], float(ref_all[..., 2].max())) if len(ref_all) else hi[2]
    pts_xyz, pts_cls = _sample_points(tiles, lo, hi, seed)
    keep = (pts_xyz[:, 2] >= lo[2] - 20) & (pts_xyz[:, 2] <= hi[2] + 40)  # drop outliers
    pts_xyz, pts_cls = pts_xyz[keep], pts_cls[keep]
    lo[2] = min(lo[2], float(pts_xyz[:, 2].min()))
    hi[2] = max(hi[2], float(pts_xyz[:, 2].max()))
    q = Quantiser.covering(lo, hi)

    w = _Writer()
    w.add("rec_pos", q.encode(rec_all).reshape(-1))
    w.add("rec_kind", np.concatenate(rec_kind) if rec_kind else np.empty(0, np.uint8))
    w.add("rec_owner", np.concatenate(rec_owner) if rec_owner else np.empty(0, np.uint16))
    w.add("pad_pos", q.encode(pad_all).reshape(-1))
    w.add("pad_owner", np.concatenate(pad_owner) if pad_owner else np.empty(0, np.uint16))
    w.add("ref_pos", q.encode(ref_all).reshape(-1))
    w.add("ref_owner", np.concatenate(ref_owner) if ref_owner else np.empty(0, np.uint16))
    w.add("pts_pos", q.encode(pts_xyz).reshape(-1))
    w.add("pts_class", pts_cls)

    by_fp: dict[str, list[BuildingResult]] = {}
    for r in results:
        if r.footprint_id is not None and r.status != "no-reference":
            by_fp.setdefault(r.footprint_id, []).append(r)
    footprints = [
        {
            "id": fid,
            "status": status,
            "reason": reason or None,
            "points": n_points,
            "density": _num(density, 1),
            "rmse": _num(_rmse(by_fp.get(fid, []))),
            "buildings": [_building(r, ref_index) for r in by_fp.get(fid, [])],
        }
        for fid, status, reason, n_points, density, _g, _run, _k, _w in recs
    ]
    ref_fp = {r.building_id: fp_index.get(r.footprint_id or "") for r in results}
    summary = summarise(results)
    statuses: dict[str, int] = {}
    for row in recs:
        statuses[row[1]] = statuses.get(row[1], 0) + 1
    index = {
        "version": 1,
        "area": area,
        "runId": max(row[6] for row in recs),
        "generated": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "crs": "EPSG:2950 + CGVD28",
        "origin": list(q.origin),
        "scale": q.scale,
        "summary": {
            "rmse": _num(summary["vertical_rmse_m"]),
            "medianBuildingRmse": _num(summary["median_building_rmse_m"]),
            "coverage": _num(summary["coverage"], 4),
            "orientationMedian": _num(summary["orientation_error_median_deg"], 2),
            "orientationP90": _num(summary["orientation_error_p90_deg"], 2),
            "buildingsReconstructed": summary["buildings_reconstructed"],
            "buildings": summary["n_buildings"],
            "footprints": len(recs),
            "footprintStatus": dict(sorted(statuses.items())),
        },
        "footprints": footprints,
        "references": [{"id": gid, "footprint": ref_fp.get(gid)} for gid in ref_ids],
        "arrays": w.index,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "scene.bin").write_bytes(b"".join(w.chunks))
    (out / "scene.json").write_text(json.dumps(index, separators=(",", ":")) + "\n")
    return index
