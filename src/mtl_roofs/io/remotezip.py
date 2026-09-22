"""Read members out of a remote ZIP archive using HTTP range requests.

Montreal publishes the 2015 LiDAR as five ZIP archives totalling 119 GB, and the
2016 LOD2 model as per-borough archives of nested per-tile ZIPs. The per-tile
download URLs the city advertises in ``indexlidar2015.csv`` are all dead (HTTP 404
as of 2026-09-22), so the archives are the only live distribution.

Both hosts serve ``206 Partial Content``, which makes it possible to fetch the ZIP64
central directory from the tail of the archive and then stream only the bytes of the
one member we want. Extracting a single 1 km LiDAR tile costs ~190 MB instead of
20 GB.

Both hosts also return HTTP 403 for non-browser user agents, so a browser
``User-Agent`` is mandatory.
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO
from urllib.request import Request, urlopen

#: Signature of a ZIP64 "end of central directory" record.
_EOCD64 = b"PK\x06\x06"
#: Signature of the classic "end of central directory" record.
_EOCD = b"PK\x05\x06"
#: Signature of a central-directory file header.
_CDFH = b"PK\x01\x02"
#: Signature of a local file header.
_LFH = b"PK\x03\x04"

#: How much of the archive tail to pull when hunting for the central directory.
_TAIL_BYTES = 70_000

_STORED = 0
_DEFLATED = 8


class RemoteZipError(RuntimeError):
    """The remote archive could not be read as a ZIP over range requests."""


class TruncatedMemberError(RemoteZipError):
    """A member's decompressed stream ended early, or ran long.

    Raised when the number of bytes recovered does not match the size the archive's
    own central directory declares. This catches a dropped connection even when the
    member has no pinned checksum yet.
    """


@dataclass(frozen=True, slots=True)
class ZipMember:
    """One entry in a remote archive's central directory."""

    name: str
    method: int
    compressed_size: int
    uncompressed_size: int
    header_offset: int

    @property
    def is_deflated(self) -> bool:
        """Whether the member is deflate-compressed rather than stored."""
        return self.method == _DEFLATED


class RemoteZip:
    """A ZIP archive accessed over HTTP range requests.

    Args:
        url: Archive URL. Must be served by a host honouring ``Range``.
        user_agent: Sent on every request; the city hosts 403 the default agent.
        timeout: Per-request timeout in seconds.
    """

    def __init__(self, url: str, user_agent: str, timeout: float = 300.0) -> None:
        self.url = url
        self.user_agent = user_agent
        self.timeout = timeout
        self._size: int | None = None
        self._members: list[ZipMember] | None = None

    # ----------------------------------------------------------------- HTTP
    def _get_range(self, start: int, end: int) -> tuple[bytes, dict[str, str]]:
        """Fetch the inclusive byte range ``[start, end]``."""
        req = Request(
            self.url,
            headers={"User-Agent": self.user_agent, "Range": f"bytes={start}-{end}"},
        )
        with urlopen(req, timeout=self.timeout) as resp:
            return resp.read(), dict(resp.headers)

    @property
    def size(self) -> int:
        """Total size of the archive in bytes, from the ``Content-Range`` header."""
        if self._size is None:
            _, headers = self._get_range(0, 0)
            content_range = headers.get("Content-Range")
            if not content_range:
                msg = f"{self.url} does not honour HTTP range requests"
                raise RemoteZipError(msg)
            self._size = int(str(content_range).split("/")[1])
        return self._size

    # -------------------------------------------------------- central directory
    def members(self) -> list[ZipMember]:
        """Parse and cache the archive's central directory."""
        if self._members is None:
            self._members = self._read_central_directory(0, self.size)
        return self._members

    def member(self, name_fragment: str) -> ZipMember:
        """Return the single member whose name contains ``name_fragment``.

        Raises:
            KeyError: if no member matches, or if more than one does.
        """
        hits = [m for m in self.members() if name_fragment in m.name]
        if not hits:
            msg = f"no member matching {name_fragment!r} in {self.url}"
            raise KeyError(msg)
        if len(hits) > 1:
            names = ", ".join(sorted(m.name for m in hits))
            msg = f"{name_fragment!r} is ambiguous, matches: {names}"
            raise KeyError(msg)
        return hits[0]

    def _read_central_directory(self, base: int, length: int) -> list[ZipMember]:
        """Read the central directory of an archive occupying ``[base, base+length)``."""
        end = base + length
        tail_len = min(length, _TAIL_BYTES)
        tail, _ = self._get_range(end - tail_len, end - 1)

        pos64 = tail.rfind(_EOCD64)
        if pos64 >= 0:
            count = struct.unpack_from("<Q", tail, pos64 + 32)[0]
            cd_size = struct.unpack_from("<Q", tail, pos64 + 40)[0]
            cd_offset = struct.unpack_from("<Q", tail, pos64 + 48)[0]
        else:
            pos = tail.rfind(_EOCD)
            if pos < 0:
                msg = f"no end-of-central-directory record in the last {tail_len} bytes"
                raise RemoteZipError(msg)
            count = struct.unpack_from("<H", tail, pos + 10)[0]
            cd_size = struct.unpack_from("<I", tail, pos + 12)[0]
            cd_offset = struct.unpack_from("<I", tail, pos + 16)[0]

        raw, _ = self._get_range(base + cd_offset, base + cd_offset + cd_size - 1)
        members = parse_central_directory(raw)
        if len(members) != count:
            msg = f"central directory declared {count} entries, parsed {len(members)}"
            raise RemoteZipError(msg)
        return members

    # ------------------------------------------------------------- extraction
    def _data_offset(self, member: ZipMember) -> int:
        """Absolute offset of a member's data, skipping its local file header."""
        head, _ = self._get_range(member.header_offset, member.header_offset + 29)
        if head[:4] != _LFH:
            msg = f"expected a local file header at {member.header_offset}, got {head[:4]!r}"
            raise RemoteZipError(msg)
        name_len, extra_len = struct.unpack_from("<HH", head, 26)
        return member.header_offset + 30 + int(name_len) + int(extra_len)

    def _open_range(self, start: int, end: int) -> IO[bytes]:
        """Open a streaming response over the inclusive byte range ``[start, end]``.

        Split out from :meth:`_stream` so that tests can serve an archive from a
        local buffer without any network.
        """
        req = Request(
            self.url,
            headers={"User-Agent": self.user_agent, "Range": f"bytes={start}-{end}"},
        )
        stream: IO[bytes] = urlopen(req, timeout=self.timeout)
        return stream

    def open_member(self, member: ZipMember, chunk_size: int = 1 << 20) -> bytes:
        """Fetch and decompress an entire member into memory.

        Only use this for members small enough to hold in RAM; prefer
        :meth:`extract_member` for LiDAR tiles and reference-model archives.
        """
        buf = bytearray()
        self._stream(member, buf.extend, chunk_size)
        return bytes(buf)

    def extract_member(
        self,
        member: ZipMember,
        dest: Path,
        chunk_size: int = 1 << 20,
    ) -> Path:
        """Stream a member to ``dest``, decompressing on the fly.

        Returns:
            The path written.
        """
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        try:
            with tmp.open("wb") as handle:
                self._stream(member, handle.write, chunk_size)
        except BaseException:
            # Never leave a partial file where a later run would mistake it for a
            # complete download. This includes KeyboardInterrupt, which is exactly
            # how a multi-hundred-megabyte fetch tends to die.
            tmp.unlink(missing_ok=True)
            raise
        tmp.replace(dest)
        return dest

    def _stream(
        self,
        member: ZipMember,
        sink: Callable[[bytes], object],
        chunk_size: int,
    ) -> None:
        """Pull a member's bytes and push the decompressed stream into ``sink``.

        Raises:
            TruncatedMemberError: if the recovered byte count disagrees with the
                size declared in the central directory.
        """
        if member.method not in (_STORED, _DEFLATED):
            msg = f"unsupported compression method {member.method} for {member.name}"
            raise RemoteZipError(msg)
        start = self._data_offset(member)
        decomp = zlib.decompressobj(-zlib.MAX_WBITS) if member.is_deflated else None
        written = 0

        def emit(data: bytes) -> None:
            nonlocal written
            written += len(data)
            sink(data)

        with self._open_range(start, start + member.compressed_size - 1) as resp:
            while chunk := resp.read(chunk_size):
                emit(decomp.decompress(chunk) if decomp else chunk)
        if decomp is not None:
            emit(decomp.flush())

        if written != member.uncompressed_size:
            msg = (
                f"{member.name}: recovered {written} bytes but the archive declares "
                f"{member.uncompressed_size}"
            )
            raise TruncatedMemberError(msg)


def parse_central_directory(raw: bytes) -> list[ZipMember]:
    """Parse raw central-directory bytes into members, honouring ZIP64 extras.

    Split out from :class:`RemoteZip` so it can be unit-tested against a ZIP built
    locally, with no network involved.
    """
    members: list[ZipMember] = []
    pos = 0
    while pos < len(raw) - 4 and raw[pos : pos + 4] == _CDFH:
        method = struct.unpack_from("<H", raw, pos + 10)[0]
        csize, usize = struct.unpack_from("<II", raw, pos + 20)
        name_len, extra_len, comment_len = struct.unpack_from("<HHH", raw, pos + 28)
        offset = struct.unpack_from("<I", raw, pos + 42)[0]
        name = raw[pos + 46 : pos + 46 + name_len].decode("latin1")
        extra = raw[pos + 46 + name_len : pos + 46 + name_len + extra_len]
        usize, csize, offset = _apply_zip64(extra, usize, csize, offset)
        members.append(ZipMember(name, method, csize, usize, offset))
        pos += 46 + name_len + extra_len + comment_len
    return members


def _apply_zip64(extra: bytes, usize: int, csize: int, offset: int) -> tuple[int, int, int]:
    """Replace 0xFFFFFFFF placeholders with the 64-bit values from the ZIP64 extra field.

    The ZIP64 extra field stores only the fields that actually overflowed, in a fixed
    order, so each value is consumed only when its 32-bit counterpart is saturated.
    """
    pos = 0
    while pos < len(extra) - 3:
        header_id, size = struct.unpack_from("<HH", extra, pos)
        if header_id == 0x0001:
            cursor = pos + 4
            out: list[int] = []
            for value in (usize, csize, offset):
                if value == 0xFFFFFFFF and cursor + 8 <= pos + 4 + size:
                    out.append(struct.unpack_from("<Q", extra, cursor)[0])
                    cursor += 8
                else:
                    out.append(value)
            usize, csize, offset = out
        pos += 4 + size
    return usize, csize, offset
