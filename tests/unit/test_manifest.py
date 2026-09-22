"""The committed manifest must stay loadable and honest."""

from __future__ import annotations

from pathlib import Path

from mtl_roofs.manifest import load_manifest, sha256_file, verify


def test_manifest_loads_and_pins_the_v0_sources() -> None:
    sources = load_manifest()
    assert "footprints_2d_2016" in sources
    assert "lidar2015_290-294" in sources
    assert "lod2_2016_cdnndg_01_12" in sources


def test_footprint_checksum_is_pinned() -> None:
    """This is the one whole-file download, so it must be verifiable."""
    source = load_manifest()["footprints_2d_2016"]
    assert source.sha256 is not None
    assert len(source.sha256) == 64
    assert source.size == 100_284_823


def test_every_url_is_https() -> None:
    for source in load_manifest().values():
        assert source.url.startswith("https://"), source.key


def test_archives_are_not_checksummed() -> None:
    """A 20 GB archive is never downloaded whole, so it cannot carry a checksum."""
    for source in load_manifest().values():
        if source.kind == "archive":
            assert source.sha256 is None, source.key


def test_sha256_and_verify_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "f.bin"
    path.write_bytes(b"montreal")
    digest = sha256_file(path)
    assert verify(path, digest)
    assert not verify(path, "0" * 64)


def test_unpinned_checksum_passes_but_is_visible(tmp_path: Path) -> None:
    path = tmp_path / "f.bin"
    path.write_bytes(b"x")
    assert verify(path, None)
