"""LiDAR tile access and LAS header inspection.

Tiles are 1000 m squares named ``<easting_km>-<northing_km>``; the member inside the
bulk archive is ``<tile>_2015.las``. See :mod:`mtl_roofs.io.remotezip` for why tiles
are pulled out of the archives rather than downloaded individually.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

#: Byte offsets into the LAS 1.2 public header block.
_OFF_VERSION = 24
_OFF_POINT_FORMAT = 104
_OFF_LEGACY_COUNT = 107
_OFF_SCALE = 131
_OFF_BOUNDS = 179
_LAS_HEADER_MIN = 227

#: ASPRS classification codes as published for this dataset.
CLASSIFICATION = {
    1: "unclassified",
    2: "ground",
    3: "low vegetation",
    4: "medium vegetation",
    5: "high vegetation",
    6: "building",
    7: "low point (noise)",
    8: "reserved",
}

#: Classes the reconstruction consumes. Class 6 is *not* assumed to be populated —
#: see docs/data-sources.md; the filter falls back to geometric separation.
ROOF_CANDIDATE_CLASSES = frozenset({1, 6})
GROUND_CLASSES = frozenset({2})
VEGETATION_CLASSES = frozenset({3, 4, 5})
NOISE_CLASSES = frozenset({7})


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
