"""What `data fetch` resolves for a study area.

The original fetch command silently handled LiDAR only, so the reference model was
never downloaded at all. These tests pin that contract down.
"""

from __future__ import annotations

import pytest

from mtl_roofs.cli import _lidar_archive_for, _plan_artifacts, _reference_archive_for
from mtl_roofs.manifest import load_manifest


@pytest.fixture(scope="module")
def plan():
    return _plan_artifacts("cdn-ndg-03")


def test_plan_covers_lidar_and_reference(plan) -> None:
    labels = sorted(item.label for item in plan.items)
    assert labels == ["lidar"] * 4 + ["reference"]


def test_every_artifact_resolves_to_an_archive(plan) -> None:
    unresolved = [i.member.artifact for i in plan.items if i.source_key is None]
    assert unresolved == []


def test_reference_artifact_is_the_citygml_not_the_nested_zip(plan) -> None:
    reference = next(i for i in plan.items if i.label == "reference")
    assert reference.member.key == "CDNNDG03"
    assert reference.member.artifact == "CDNNDG03_2016.gml"


def test_every_v0_artifact_is_pinned(plan) -> None:
    unpinned = [i.member.artifact for i in plan.items if not i.member.is_pinned]
    assert unpinned == []


def test_lidar_artifacts_are_the_four_measured_tiles(plan) -> None:
    names = sorted(i.member.artifact for i in plan.items if i.label == "lidar")
    assert names == [
        "292-5034_2015.las",
        "292-5035_2015.las",
        "293-5034_2015.las",
        "293-5035_2015.las",
    ]


@pytest.mark.parametrize(
    ("tile", "expected"),
    [
        ("292-5034", "lidar2015_290-294"),
        ("266-5030", "lidar2015_266-279"),
        ("307-5040", "lidar2015_300-307"),
        ("999-5000", None),
    ],
)
def test_lidar_tiles_map_to_the_right_easting_band(tile: str, expected: str | None) -> None:
    assert _lidar_archive_for(tile, load_manifest()) == expected


@pytest.mark.parametrize(
    ("tile", "expected"),
    [
        ("CDNNDG03", "lod2_2016_cdnndg_01_12"),
        ("O01", "lod2_2016_o_01_03"),
        ("PMR06", "lod2_2016_pmr_01_11"),
        ("XYZ99", None),
    ],
)
def test_reference_tiles_map_to_the_right_borough(tile: str, expected: str | None) -> None:
    assert _reference_archive_for(tile, load_manifest()) == expected


def test_other_areas_still_resolve_even_though_they_are_unpinned() -> None:
    """Outremont and the Plateau are not pinned yet; that must be reported, not fatal."""
    for area in ("outremont-01", "plateau-06"):
        plan = _plan_artifacts(area)
        assert plan.items
        assert all(i.source_key is not None for i in plan.items)
        assert any(not i.member.is_pinned for i in plan.items)
