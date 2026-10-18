"""LiDAR tile access and LAS header inspection.

Tiles are 1000 m squares named ``<easting_km>-<northing_km>``; the member inside the
bulk archive is ``<tile>_2015.las``. See :mod:`mtl_roofs.io.remotezip` for why tiles
are pulled out of the archives rather than downloaded individually.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

#: Byte offsets into the LAS 1.2 public header block.
_OFF_VERSION = 24
_OFF_POINT_FORMAT = 104
_OFF_LEGACY_COUNT = 107
_OFF_SCALE = 131
_OFF_BOUNDS = 179
_LAS_HEADER_MIN = 227

#: ASPRS classification codes as published for this dataset, plus class 28, which the
#: city does not document; see docs/data-sources.md for what it was measured to be.
CLASSIFICATION = {
    1: "unclassified",
    2: "ground",
    3: "low vegetation",
    4: "medium vegetation",
    5: "high vegetation",
    6: "building",
    7: "low point (noise)",
    8: "reserved",
    28: "undocumented",
}


@dataclass(frozen=True, slots=True)
class PointSet:
    """Point coordinates and their classification codes, row-aligned."""

    xyz: npt.NDArray[np.float64]
    classification: npt.NDArray[np.uint8]

    def __post_init__(self) -> None:
        """Reject misaligned arrays at construction, not at first use."""
        if self.xyz.ndim != 2 or self.xyz.shape[1] != 3:
            msg = f"expected an (n, 3) xyz array, got shape {self.xyz.shape}"
            raise ValueError(msg)
        if self.classification.shape != (len(self.xyz),):
            msg = f"{len(self.xyz)} points but {self.classification.shape} classes"
            raise ValueError(msg)

    def __len__(self) -> int:
        """Number of points."""
        return len(self.xyz)


def read_points(path: Path) -> PointSet:
    """Read a LAS or LAZ file into a :class:`PointSet`, with scaled coordinates."""
    import laspy

    las = laspy.read(path)
    xyz = np.column_stack([np.asarray(las.x), np.asarray(las.y), np.asarray(las.z)])
    return PointSet(xyz.astype(np.float64), np.asarray(las.classification, dtype=np.uint8))


@dataclass(frozen=True, slots=True)
class LasHeader:
    """The subset of the LAS public header this project relies on."""

    version: str
    point_format: int
    point_count: int
    xmin: float
    xmax: float
    ymin: float
    ymax: float
    zmin: float
    zmax: float

    @property
    def area_m2(self) -> float:
        """Planimetric extent of the tile in square metres."""
        return (self.xmax - self.xmin) * (self.ymax - self.ymin)

    @property
    def density(self) -> float:
        """Average point density over the tile's bounding box, in points per m²."""
        area = self.area_m2
        return self.point_count / area if area > 0 else 0.0


def parse_las_header(raw: bytes) -> LasHeader:
    """Parse a LAS public header block from the first bytes of a file.

    Args:
        raw: At least the first 227 bytes of a LAS file.

    Raises:
        ValueError: if the signature is not ``LASF`` or the buffer is too short.
    """
    if len(raw) < _LAS_HEADER_MIN:
        msg = f"need at least {_LAS_HEADER_MIN} bytes of LAS header, got {len(raw)}"
        raise ValueError(msg)
    if raw[:4] != b"LASF":
        msg = f"not a LAS file: signature {raw[:4]!r}"
        raise ValueError(msg)
    version = f"{raw[_OFF_VERSION]}.{raw[_OFF_VERSION + 1]}"
    point_format = raw[_OFF_POINT_FORMAT] & 0x3F
    count = struct.unpack_from("<I", raw, _OFF_LEGACY_COUNT)[0]
    xmax, xmin, ymax, ymin, zmax, zmin = struct.unpack_from("<6d", raw, _OFF_BOUNDS)
    return LasHeader(version, point_format, count, xmin, xmax, ymin, ymax, zmin, zmax)


def tile_name(easting: float, northing: float) -> str:
    """Name of the 1 km tile containing a projected coordinate."""
    return f"{int(easting // 1000)}-{int(northing // 1000)}"


def tile_member(tile: str) -> str:
    """Archive member name for a tile, e.g. ``292-5034`` -> ``292-5034_2015.las``."""
    return f"{tile}_2015.las"


def read_header(path: Path) -> LasHeader:
    """Read the LAS public header from a local file."""
    with path.open("rb") as handle:
        return parse_las_header(handle.read(_LAS_HEADER_MIN))


@dataclass(frozen=True, slots=True)
class PointIndex:
    """Points of a study area, bucketed on a coarse grid for fast window queries.

    A whole area is ~30 million points; a footprint needs a few thousand. Points are
    sorted by bucket once, so a window query only touches the buckets it overlaps.

    Attributes:
        points: All points, sorted by bucket.
        bucket_m: Bucket size in metres.
        keys: Sorted unique bucket keys present.
        starts: Start offset of each key's points in ``points``.
    """

    points: PointSet
    bucket_m: float
    keys: npt.NDArray[np.int64]
    starts: npt.NDArray[np.intp]

    @classmethod
    def from_las(
        cls,
        paths: list[Path],
        bbox: tuple[float, float, float, float],
        keep_classes: frozenset[int] | None = None,
        bucket_m: float = 20.0,
        chunk_size: int = 2_000_000,
    ) -> PointIndex:
        """Read the points of ``paths`` inside ``bbox``, streaming each file in chunks.

        Args:
            paths: LAS or LAZ files.
            bbox: ``(xmin, ymin, xmax, ymax)`` to keep, EPSG:2950.
            keep_classes: Classification codes to keep; all if ``None``.
            bucket_m: Bucket size of the index.
            chunk_size: Points read at a time, which bounds the memory a tile needs.
        """
        import laspy

        xmin, ymin, xmax, ymax = bbox
        xyz_parts: list[npt.NDArray[np.float64]] = []
        cls_parts: list[npt.NDArray[np.uint8]] = []
        for path in paths:
            with laspy.open(path) as reader:
                for chunk in reader.chunk_iterator(chunk_size):
                    x, y = np.asarray(chunk.x), np.asarray(chunk.y)
                    classes = np.asarray(chunk.classification, dtype=np.uint8)
                    keep = (x >= xmin) & (x <= xmax) & (y >= ymin) & (y <= ymax)
                    if keep_classes is not None:
                        keep &= np.isin(classes, list(keep_classes))
                    xyz_parts.append(np.column_stack([x[keep], y[keep], np.asarray(chunk.z)[keep]]))
                    cls_parts.append(classes[keep])
        xyz = np.concatenate(xyz_parts) if xyz_parts else np.empty((0, 3))
        classes = np.concatenate(cls_parts) if cls_parts else np.empty(0, dtype=np.uint8)
        return cls.build(PointSet(xyz.astype(np.float64), classes), bucket_m)

    @classmethod
    def build(cls, points: PointSet, bucket_m: float = 20.0) -> PointIndex:
        """Index points already in memory."""
        key = _bucket_key(points.xyz[:, 0], points.xyz[:, 1], bucket_m)
        order = np.argsort(key, kind="stable")
        key = key[order]
        keys, starts = np.unique(key, return_index=True)
        sorted_points = PointSet(points.xyz[order], points.classification[order])
        return cls(sorted_points, bucket_m, keys, starts.astype(np.intp))

    def __len__(self) -> int:
        """Number of indexed points."""
        return len(self.points)

    def query(self, xmin: float, ymin: float, xmax: float, ymax: float) -> PointSet:
        """The points inside a window, in no particular order."""
        bx = np.arange(np.floor(xmin / self.bucket_m), np.floor(xmax / self.bucket_m) + 1)
        by = np.arange(np.floor(ymin / self.bucket_m), np.floor(ymax / self.bucket_m) + 1)
        wanted = _bucket_key(
            np.repeat(bx, len(by)) * self.bucket_m,
            np.tile(by, len(bx)) * self.bucket_m,
            self.bucket_m,
        )
        pos = np.searchsorted(self.keys, wanted)
        present = (pos < len(self.keys)) & (
            self.keys[np.minimum(pos, len(self.keys) - 1)] == wanted
        )
        ends = np.r_[self.starts[1:], len(self.points)]
        ranges = [np.arange(self.starts[p], ends[p]) for p in pos[present]]
        idx = np.concatenate(ranges) if ranges else np.empty(0, dtype=np.intp)
        xyz = self.points.xyz[idx]
        inside = (
            (xyz[:, 0] >= xmin) & (xyz[:, 0] <= xmax) & (xyz[:, 1] >= ymin) & (xyz[:, 1] <= ymax)
        )
        return PointSet(xyz[inside], self.points.classification[idx][inside])


def _bucket_key(
    x: npt.NDArray[np.float64], y: npt.NDArray[np.float64], bucket_m: float
) -> npt.NDArray[np.int64]:
    """One integer per bucket: column and row packed into 64 bits."""
    col = np.floor(np.asarray(x) / bucket_m).astype(np.int64)
    row = np.floor(np.asarray(y) / bucket_m).astype(np.int64)
    return (col << 32) + row
