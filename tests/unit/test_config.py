"""Study areas and bbox arithmetic."""

from __future__ import annotations

import pytest

from mtl_roofs.config import AREAS_DIR, BBox, StudyArea


def test_v0_area_loads_and_matches_the_measured_building_count() -> None:
    area = StudyArea.load("cdn-ndg-03")
    assert area.epsg == 2950
    assert area.expected_buildings == 1109
    assert area.reference_tiles == ["CDNNDG03"]


def test_v0_area_needs_exactly_the_four_measured_tiles() -> None:
    area = StudyArea.load("cdn-ndg-03")
    assert sorted(area.bbox.lidar_tiles()) == [
        "292-5034",
        "292-5035",
        "293-5034",
        "293-5035",
    ]


def test_every_committed_area_is_valid() -> None:
    names = sorted(p.stem for p in AREAS_DIR.glob("*.yaml"))
    assert names, "no study areas are committed"
    for name in names:
        area = StudyArea.load(name)
        assert area.bbox.area_km2 > 0
        assert area.bbox.lidar_tiles()


def test_unknown_area_lists_what_is_available() -> None:
    with pytest.raises(FileNotFoundError, match="cdn-ndg-03"):
        StudyArea.load("does-not-exist")


def test_bbox_rejects_inverted_corners() -> None:
    with pytest.raises(ValueError, match="degenerate bbox"):
        BBox(xmin=10, ymin=0, xmax=5, ymax=10)


def test_bbox_area_is_in_square_kilometres() -> None:
    assert BBox(xmin=0, ymin=0, xmax=1000, ymax=1000).area_km2 == pytest.approx(1.0)


def test_tiles_cover_a_bbox_spanning_a_tile_boundary() -> None:
    bbox = BBox(xmin=291999.0, ymin=5034999.0, xmax=292001.0, ymax=5035001.0)
    assert sorted(bbox.lidar_tiles()) == ["291-5034", "291-5035", "292-5034", "292-5035"]
