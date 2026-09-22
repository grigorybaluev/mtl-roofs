"""Per-member checksum pinning: the integrity contract for everything we download."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mtl_roofs.manifest import (
    ChecksumMismatchError,
    Member,
    load_manifest,
    member_yaml_block,
    sha256_file,
    verify,
    verify_or_raise,
)

MINIMAL = {
    "sources": {
        "arch": {
            "kind": "archive",
            "url": "https://example.invalid/a.zip",
            "members": {
                "tile_2015.las": {"sha256": "a" * 64, "size": 123},
                "CDNNDG03": {
                    "artifact": "CDNNDG03_2016.gml",
                    "sha256": "b" * 64,
                    "size": 456,
                },
                "unpinned.las": {},
            },
        }
    }
}


def write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_member_artifact_defaults_to_its_key(tmp_path: Path) -> None:
    source = load_manifest(write(tmp_path, MINIMAL))["arch"]
    assert source.members["tile_2015.las"].artifact == "tile_2015.las"


def test_member_artifact_can_differ_from_its_key(tmp_path: Path) -> None:
    """The reference model's member is a nested ZIP; only its .gml is kept."""
    source = load_manifest(write(tmp_path, MINIMAL))["arch"]
    assert source.members["CDNNDG03"].artifact == "CDNNDG03_2016.gml"


def test_member_without_sha256_is_unpinned(tmp_path: Path) -> None:
    source = load_manifest(write(tmp_path, MINIMAL))["arch"]
    assert not source.members["unpinned.las"].is_pinned
    assert source.members["tile_2015.las"].is_pinned


def test_unknown_member_returns_an_unpinned_placeholder(tmp_path: Path) -> None:
    """A member we have not pinned yet is reportable, not an error."""
    source = load_manifest(write(tmp_path, MINIMAL))["arch"]
    placeholder = source.member("never-seen.las")
    assert placeholder.artifact == "never-seen.las"
    assert not placeholder.is_pinned


def test_a_malformed_checksum_is_rejected_at_load(tmp_path: Path) -> None:
    bad = {
        "sources": {
            "arch": {
                "kind": "archive",
                "url": "https://x.invalid/a.zip",
                "members": {"t.las": {"sha256": "abc"}},
            }
        }
    }
    with pytest.raises(ValueError, match="64 hex characters"):
        load_manifest(write(tmp_path, bad))


def test_an_archive_may_not_carry_its_own_checksum(tmp_path: Path) -> None:
    """A 20 GB archive is never downloaded whole, so checksumming it is meaningless."""
    bad = {
        "sources": {
            "arch": {"kind": "archive", "url": "https://x.invalid/a.zip", "sha256": "c" * 64}
        }
    }
    with pytest.raises(ValueError, match="never downloaded whole"):
        load_manifest(write(tmp_path, bad))


def test_verify_or_raise_reports_both_digests(tmp_path: Path) -> None:
    path = tmp_path / "f.bin"
    path.write_bytes(b"montreal")
    actual = sha256_file(path)
    with pytest.raises(ChecksumMismatchError) as excinfo:
        verify_or_raise(path, "d" * 64)
    assert excinfo.value.expected == "d" * 64
    assert excinfo.value.actual == actual


def test_verify_or_raise_accepts_a_match(tmp_path: Path) -> None:
    path = tmp_path / "f.bin"
    path.write_bytes(b"montreal")
    verify_or_raise(path, sha256_file(path))


def test_unpinned_verifies_true_but_is_visible(tmp_path: Path) -> None:
    path = tmp_path / "f.bin"
    path.write_bytes(b"x")
    assert verify(path, None)
    verify_or_raise(path, None)


def test_yaml_block_round_trips_into_a_loadable_manifest(tmp_path: Path) -> None:
    """The block `data checksums` prints must parse back to the same members."""
    members = [
        Member(key="b.las", artifact="b.las", sha256="1" * 64, size=2),
        Member(key="A03", artifact="A03_2016.gml", sha256="2" * 64, size=3),
    ]
    block = member_yaml_block(members, indent=4)
    document = "sources:\n  arch:\n    kind: archive\n    url: https://x.invalid/a.zip\n" + block
    path = tmp_path / "manifest.yaml"
    path.write_text(document)

    parsed = load_manifest(path)["arch"].members
    assert parsed["b.las"].sha256 == "1" * 64
    assert parsed["A03"].artifact == "A03_2016.gml"
    assert parsed["A03"].size == 3


def test_yaml_block_is_sorted_so_diffs_stay_readable() -> None:
    members = [
        Member(key="z.las", artifact="z.las", sha256="1" * 64, size=1),
        Member(key="a.las", artifact="a.las", sha256="2" * 64, size=1),
    ]
    block = member_yaml_block(members)
    assert block.index("a.las") < block.index("z.las")


# --------------------------------------------------------- the real manifest
def test_v0_area_artifacts_are_all_pinned() -> None:
    """Guards the pins themselves: issue #13's headline acceptance criterion."""
    sources = load_manifest()
    lidar = sources["lidar2015_290-294"].members
    for tile in ("292-5034", "292-5035", "293-5034", "293-5035"):
        member = lidar[f"{tile}_2015.las"]
        assert member.is_pinned, tile
        assert member.size is not None, tile
        assert member.size > 100_000_000, tile

    reference = sources["lod2_2016_cdnndg_01_12"].members["CDNNDG03"]
    assert reference.is_pinned
    assert reference.artifact == "CDNNDG03_2016.gml"


def test_every_pinned_checksum_is_well_formed() -> None:
    for source in load_manifest().values():
        for member in source.members.values():
            if member.is_pinned:
                assert member.sha256 is not None
                assert len(member.sha256) == 64
                int(member.sha256, 16)  # raises if not hex
