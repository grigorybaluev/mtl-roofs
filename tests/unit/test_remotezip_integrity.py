"""Download integrity: truncation must fail loudly and leave no partial file behind.

The archive is served from a local buffer by overriding the two range primitives, so
these run with no network.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import IO

import pytest

from mtl_roofs.io.remotezip import RemoteZip, TruncatedMemberError

PAYLOAD = b"LASF" + bytes(range(256)) * 400


class LocalZip(RemoteZip):
    """A RemoteZip backed by bytes in memory, optionally truncating its responses."""

    def __init__(self, data: bytes, cut: int = 0) -> None:
        super().__init__("https://example.invalid/archive.zip", "test-agent")
        self._data = data
        self._cut = cut

    def _get_range(self, start: int, end: int) -> tuple[bytes, dict[str, str]]:
        chunk = self._data[start : end + 1]
        return chunk, {"Content-Range": f"bytes {start}-{end}/{len(self._data)}"}

    def _open_range(self, start: int, end: int) -> IO[bytes]:
        chunk = self._data[start : end + 1]
        if self._cut:
            chunk = chunk[: -self._cut]
        return io.BytesIO(chunk)


def build_archive(compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        zf.writestr("292-5034_2015.las", PAYLOAD)
        zf.writestr("other.txt", b"unrelated")
    return buf.getvalue()


@pytest.fixture
def archive() -> bytes:
    return build_archive()


def test_a_complete_member_round_trips(archive: bytes, tmp_path: Path) -> None:
    zipped = LocalZip(archive)
    dest = tmp_path / "292-5034_2015.las"
    zipped.extract_member(zipped.member("292-5034_2015.las"), dest)
    assert dest.read_bytes() == PAYLOAD


def test_a_truncated_stream_raises(archive: bytes, tmp_path: Path) -> None:
    """A dropped connection is caught even with no checksum pinned."""
    zipped = LocalZip(archive, cut=200)
    with pytest.raises(TruncatedMemberError, match="recovered"):
        zipped.extract_member(zipped.member("292-5034_2015.las"), tmp_path / "out.las")


def test_a_truncated_stream_leaves_no_file_behind(archive: bytes, tmp_path: Path) -> None:
    """The next run must not mistake a partial download for a finished one."""
    zipped = LocalZip(archive, cut=200)
    dest = tmp_path / "out.las"
    with pytest.raises(TruncatedMemberError):
        zipped.extract_member(zipped.member("292-5034_2015.las"), dest)
    assert not dest.exists()
    assert list(tmp_path.iterdir()) == []


def test_truncation_is_caught_for_stored_members(tmp_path: Path) -> None:
    """A stored member has no compression to notice the damage, so the size check must."""
    zipped = LocalZip(build_archive(zipfile.ZIP_STORED), cut=64)
    with pytest.raises(TruncatedMemberError):
        zipped.extract_member(zipped.member("292-5034_2015.las"), tmp_path / "out.las")


def test_interrupt_also_cleans_up(archive: bytes, tmp_path: Path) -> None:
    """KeyboardInterrupt is how a multi-hundred-megabyte fetch usually dies."""
    zipped = LocalZip(archive)
    dest = tmp_path / "out.las"

    def boom(_data: bytes) -> None:
        raise KeyboardInterrupt

    original = zipped._stream
    zipped._stream = lambda member, sink, chunk: boom(b"")  # type: ignore[method-assign]
    with pytest.raises(KeyboardInterrupt):
        zipped.extract_member(zipped.member("292-5034_2015.las"), dest)
    zipped._stream = original  # type: ignore[method-assign]
    assert not dest.exists()
    assert list(tmp_path.iterdir()) == []


def test_member_lookup_rejects_an_ambiguous_fragment(archive: bytes) -> None:
    """Both members contain a dot, so the fragment cannot identify one of them."""
    zipped = LocalZip(archive)
    with pytest.raises(KeyError, match="ambiguous"):
        zipped.member(".")


def test_member_lookup_rejects_an_unknown_fragment(archive: bytes) -> None:
    zipped = LocalZip(archive)
    with pytest.raises(KeyError, match="no member"):
        zipped.member("293-9999_2015.las")
