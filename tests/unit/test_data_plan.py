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


def test_plan_covers_lidar_reference_and_footprints(plan) -> None:
    labels = sorted(item.label for item in plan.items)
    assert labels == ["footprints"] + ["lidar"] * 4 + ["reference"]


def test_footprints_are_the_whole_pinned_zip(plan) -> None:
    """Footprints are downloaded whole, so the pin is the source's own checksum."""
    footprints = next(i for i in plan.items if i.label == "footprints")
    source = plan.sources[footprints.source_key]
    assert not source.is_archive
    assert footprints.member.artifact == "batiments_2d_2016_arrondissements.zip"
    assert footprints.member.sha256 == source.sha256
    assert footprints.member.size == source.size


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


# ------------------------------------------------------------------ Verdun (#91)
def test_verdun_reference_tiles_resolve_to_the_verdun_archive() -> None:
    assert _reference_archive_for("V01", load_manifest()) == "lod2_2016_v_01_09"


def test_only_narrows_the_plan_to_the_named_kinds() -> None:
    plan = _plan_artifacts("verdun", ["reference"])
    assert {i.label for i in plan.items} == {"reference"}
    assert len(plan.items) == 9


def test_only_rejects_an_unknown_kind() -> None:
    import typer

    with pytest.raises(typer.BadParameter, match="unknown artefact kind"):
        _plan_artifacts("verdun", ["lidar", "textures"])


class _FakeArchive:
    def __init__(self, names: list[str]) -> None:
        from mtl_roofs.io.remotezip import ZipMember

        self._members = [ZipMember(n, 8, 1, 1, i) for i, n in enumerate(names)]

    def members(self) -> list:  # type: ignore[type-arg]
        return self._members

    def member(self, fragment: str):  # type: ignore[no-untyped-def]
        hits = [m for m in self._members if fragment in m.name]
        if len(hits) != 1:
            raise KeyError(fragment)
        return hits[0]


def _reference_item(tile: str):  # type: ignore[no-untyped-def]
    from mtl_roofs.cli import _Artifact
    from mtl_roofs.manifest import Member

    return _Artifact(Member(tile, f"{tile}_2016.gml"), "src", "reference")


def test_flat_layout_reads_the_top_level_gml_directly() -> None:
    from mtl_roofs.cli import _resolve_member

    archive = _FakeArchive(["V01_2016.gml", "V01_2016_Appearance/", "V01_2016_Appearance/a.jpg"])
    member, nested = _resolve_member(archive, _reference_item("V01"))  # type: ignore[arg-type]
    assert (member.name, nested) == ("V01_2016.gml", False)


def test_nested_layout_still_reads_the_per_tile_zip() -> None:
    from mtl_roofs.cli import _resolve_member

    archive = _FakeArchive(["CDNNDG03.zip", "CDNNDG04.zip"])
    member, nested = _resolve_member(archive, _reference_item("CDNNDG03"))  # type: ignore[arg-type]
    assert (member.name, nested) == ("CDNNDG03.zip", True)
