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
