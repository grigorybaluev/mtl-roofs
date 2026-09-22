"""LAS header parsing and tile naming."""

from __future__ import annotations

import struct

import pytest

from mtl_roofs.io.lidar import (
    GROUND_CLASSES,
    parse_las_header,
    tile_member,
    tile_name,
)


def make_las_header(
    *,
    count: int = 18_094_054,
    xmin: float = 290000.0,
    xmax: float = 291000.0,
    ymin: float = 5035000.0,
    ymax: float = 5036000.0,
    zmin: float = 35.27,
    zmax: float = 83.02,
) -> bytes:
    """Build a LAS 1.2 public header matching the real tile 290-5035."""
    raw = bytearray(227)
    raw[0:4] = b"LASF"
    raw[24] = 1
    raw[25] = 2
    raw[104] = 1
    struct.pack_into("<I", raw, 107, count)
    struct.pack_into("<6d", raw, 179, xmax, xmin, ymax, ymin, zmax, zmin)
    return bytes(raw)


def test_parses_the_real_tile_geometry() -> None:
    header = parse_las_header(make_las_header())
    assert header.version == "1.2"
    assert header.point_format == 1
    assert header.point_count == 18_094_054
    assert header.area_m2 == pytest.approx(1_000_000.0)


def test_density_matches_the_measured_value() -> None:
    """Tile 290-5035 was measured at 18.09 points/m² across all classes."""
    assert parse_las_header(make_las_header()).density == pytest.approx(18.09, abs=0.01)


def test_rejects_a_non_las_file() -> None:
    raw = bytearray(make_las_header())
    raw[0:4] = b"PK\x03\x04"
    with pytest.raises(ValueError, match="not a LAS file"):
        parse_las_header(bytes(raw))


def test_rejects_a_truncated_header() -> None:
    with pytest.raises(ValueError, match="at least 227 bytes"):
        parse_las_header(b"LASF")


def test_zero_area_tile_has_zero_density() -> None:
    header = parse_las_header(make_las_header(xmin=1.0, xmax=1.0))
    assert header.density == 0.0


@pytest.mark.parametrize(
    ("easting", "northing", "expected"),
    [
        (292347.25, 5034885.0, "292-5034"),
        (293810.3125, 5035969.5, "293-5035"),
        (290000.0, 5035000.0, "290-5035"),
    ],
)
def test_tile_naming_matches_the_city_scheme(
    easting: float, northing: float, expected: str
) -> None:
    assert tile_name(easting, northing) == expected


def test_member_name_matches_the_archive() -> None:
    assert tile_member("292-5034") == "292-5034_2015.las"


def test_ground_class_is_two() -> None:
    assert {2} == GROUND_CLASSES
