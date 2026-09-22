"""The remote-archive reader, tested against a ZIP built locally.

The parsing half is exercised with no network: a real ZIP is written to disk, its
central directory is sliced out and fed to the same parser the HTTP path uses.
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path

import pytest

from mtl_roofs.io.remotezip import ZipMember, parse_central_directory


def _central_directory_bytes(path: Path) -> bytes:
    """Slice the central directory out of a local ZIP file."""
    raw = path.read_bytes()
    eocd = raw.rfind(b"PK\x05\x06")
    size = struct.unpack_from("<I", raw, eocd + 12)[0]
    offset = struct.unpack_from("<I", raw, eocd + 16)[0]
    return raw[offset : offset + size]


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    path = tmp_path / "sample.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("292-5034_2015.las", b"LASF" + b"\0" * 400)
        zf.writestr("nested/CDNNDG03_2016_GML.zip", b"x" * 1000)
        zf.writestr("stored.txt", b"y" * 10, compress_type=zipfile.ZIP_STORED)
    return path


def test_parses_every_member(archive: Path) -> None:
    members = parse_central_directory(_central_directory_bytes(archive))
    assert [m.name for m in members] == [
        "292-5034_2015.las",
        "nested/CDNNDG03_2016_GML.zip",
        "stored.txt",
    ]


def test_records_compression_method(archive: Path) -> None:
    members = {m.name: m for m in parse_central_directory(_central_directory_bytes(archive))}
    assert members["292-5034_2015.las"].is_deflated
    assert not members["stored.txt"].is_deflated


def test_offsets_point_at_local_headers(archive: Path) -> None:
    raw = archive.read_bytes()
    for member in parse_central_directory(_central_directory_bytes(archive)):
        assert raw[member.header_offset : member.header_offset + 4] == b"PK\x03\x04"


def test_uncompressed_sizes_are_recovered(archive: Path) -> None:
    members = {m.name: m for m in parse_central_directory(_central_directory_bytes(archive))}
    assert members["stored.txt"].uncompressed_size == 10
    assert members["nested/CDNNDG03_2016_GML.zip"].uncompressed_size == 1000


def test_empty_directory_yields_no_members() -> None:
    assert parse_central_directory(b"") == []


def test_zip64_extra_field_overrides_saturated_values() -> None:
    """A ZIP64 extra supplies only the fields that actually overflowed, in order."""
    from mtl_roofs.io.remotezip import _apply_zip64

    extra = struct.pack("<HHQQ", 0x0001, 16, 5_000_000_000, 4_000_000_000)
    usize, csize, offset = _apply_zip64(extra, 0xFFFFFFFF, 0xFFFFFFFF, 123)
    assert (usize, csize, offset) == (5_000_000_000, 4_000_000_000, 123)


def test_member_is_hashable_and_frozen() -> None:
    member = ZipMember("a.las", 8, 10, 20, 30)
    assert {member}
    with pytest.raises(AttributeError):
        member.name = "b.las"  # type: ignore[misc]
