"""Sampling roof surfaces on the common evaluation grid (``docs/evaluation.md`` §6).

Cells are ``CELL_M`` squares aligned on multiples of ``CELL_M`` in EPSG:2950, so every
building and every run share one grid. A cell is sampled at its centre and belongs to
a reference building if the centre lies in the building's roof outline eroded by one
cell. Each surface is a height per cell, ``NaN`` where it is not defined.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import numpy.typing as npt
import shapely

from mtl_roofs.geometry.roof import RoofFace, plane_height
from mtl_roofs.io.reference import Point3, newell_normal

FloatArray = npt.NDArray[np.float64]

#: Evaluation grid cell size, metres.
CELL_M = 0.5


def building_cells(outline: Any, cell: float = CELL_M) -> tuple[FloatArray, FloatArray]:
    """Centres of the grid cells that belong to a reference building.

    Args:
        outline: The building's 2D roof outline, EPSG:2950.
        cell: Grid cell size.

    Returns:
        ``(x, y)`` of the cell centres inside the outline eroded by one cell; empty
        for a building too narrow to keep any.
    """
    eroded = shapely.buffer(outline, -cell)
    if eroded.is_empty:
        return np.empty(0), np.empty(0)
    xmin, ymin, xmax, ymax = eroded.bounds
    cols = np.arange(np.floor(xmin / cell), np.ceil(xmax / cell))
    rows = np.arange(np.floor(ymin / cell), np.ceil(ymax / cell))
    mesh_x, mesh_y = np.meshgrid((cols + 0.5) * cell, (rows + 0.5) * cell)
    gx, gy = mesh_x.ravel(), mesh_y.ravel()
    shapely.prepare(eroded)
    inside = shapely.contains_xy(eroded, gx, gy)
    return gx[inside].astype(np.float64), gy[inside].astype(np.float64)


def reference_surface(
    roofs: Sequence[Sequence[Point3]], x: FloatArray, y: FloatArray
) -> FloatArray:
    """Height of the highest reference roof surface above each point.

    The highest, because where reference surfaces overlap in plan (a dormer over the
    face it sits on, an overhang) the top one is what the LiDAR sees. Vertical and
    degenerate polygons are skipped.
    """
    z = np.full(len(x), np.nan)
    for ring in roofs:
        if len(ring) < 4:
            continue
        try:
            nx, ny, nz = newell_normal(list(ring))
        except ValueError:
            continue
        if abs(nz) < 1e-6:
            continue
        polygon = shapely.Polygon([p[:2] for p in ring])
        if not polygon.is_valid or polygon.area <= 0:
            polygon = shapely.make_valid(polygon)
        shapely.prepare(polygon)
        inside = shapely.intersects_xy(polygon, x, y)
        if not inside.any():
            continue
        cx, cy, cz = np.mean(np.asarray(ring[:-1], dtype=np.float64), axis=0)
        height = cz - (nx * (x[inside] - cx) + ny * (y[inside] - cy)) / nz
        z[inside] = np.fmax(z[inside], height)
    return z


def predicted_surface(faces: Sequence[RoofFace], x: FloatArray, y: FloatArray) -> FloatArray:
    """Height of a reconstruction's roof above each point; ``NaN`` off its faces."""
    z = np.full(len(x), np.nan)
    for face in faces:
        shapely.prepare(face.polygon)
        inside = shapely.intersects_xy(face.polygon, x, y)
        if inside.any():
            z[inside] = np.fmax(z[inside], plane_height(face.plane, x[inside], y[inside]))
    return z


def lidar_surface(
    points: FloatArray, x: FloatArray, y: FloatArray, cell: float = CELL_M
) -> FloatArray:
    """Median height of the points in each grid cell centred on ``(x, y)``.

    Used for the temporal flag, which compares the LiDAR itself, not the
    reconstruction, with the reference. ``NaN`` for a cell holding no point.
    """
    out = np.full(len(x), np.nan)
    pts = np.asarray(points, dtype=np.float64)
    if len(pts) == 0 or len(x) == 0:
        return out
    pc = np.floor(pts[:, :2] / cell).astype(np.int64)
    qc = np.floor(np.column_stack([x, y]) / cell).astype(np.int64)
    order = np.lexsort((pts[:, 2], pc[:, 1], pc[:, 0]))
    keys = pc[order]
    z = pts[order, 2]
    starts = np.flatnonzero(np.r_[True, np.any(keys[1:] != keys[:-1], axis=1)])
    ends = np.r_[starts[1:], len(keys)]
    lo, hi = z[starts + (ends - starts - 1) // 2], z[starts + (ends - starts) // 2]
    medians = {
        (int(k[0]), int(k[1])): float(m) for k, m in zip(keys[starts], (lo + hi) / 2, strict=True)
    }
    for i, (cx, cy) in enumerate(qc):
        out[i] = medians.get((int(cx), int(cy)), np.nan)
    return out
