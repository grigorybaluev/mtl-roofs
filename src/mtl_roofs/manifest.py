"""The committed data manifest: what to download, from where, and its checksum.

``data/manifest.yaml`` is the single source of truth for every byte this project
ingests. Nothing is fetched from a URL that is not pinned there.

Checksums are recorded against the **artefact that lands in ``data/raw/``**, never
against the enclosing archive: the city's LiDAR archives are 20-28 GB each and are
never downloaded whole, so they have no checksum of their own.

Pinning is deliberately a human act. ``mtl-roofs data checksums`` computes and prints
the YAML to paste, but nothing writes to the manifest automatically — a tool that
pinned whatever it happened to download would certify a corrupted file just as
happily as a good one.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from mtl_roofs.config import REPO_ROOT

MANIFEST_PATH = REPO_ROOT / "data" / "manifest.yaml"

#: Read in this many bytes at a time when checksumming a multi-hundred-MB file.
_HASH_CHUNK = 1 << 20

_SHA256_LENGTH = 64


class ChecksumMismatchError(RuntimeError):
    """A downloaded artefact does not match its pinned checksum."""

    def __init__(self, path: Path, expected: str, actual: str) -> None:
        self.path = path
        self.expected = expected
        self.actual = actual
        super().__init__(f"{path.name}: expected sha256 {expected}, got {actual}")


@dataclass(frozen=True, slots=True)
class Member:
    """One artefact extracted from a remote archive.

    Attributes:
        key: Fragment identifying the member inside the archive. For LiDAR this is
            the member filename (``292-5034_2015.las``); for the reference model it
            is a tile name (``CDNNDG03``) that matches a nested archive.
        artifact: Filename written into ``data/raw/<area>/``. For the reference
            model this differs from ``key``, because the nested archive is unpacked
            and only the CityGML is kept.
        sha256: Pinned checksum of ``artifact``, or ``None`` when not yet pinned.
        size: Expected size of ``artifact`` in bytes.
    """

    key: str
    artifact: str
    sha256: str | None = None
    size: int | None = None

    @property
    def is_pinned(self) -> bool:
        """Whether this member has a checksum recorded."""
        return self.sha256 is not None


@dataclass(frozen=True, slots=True)
class Source:
    """One pinned source: a whole file, or an archive read member by member."""

    key: str
    url: str
    kind: str
    sha256: str | None = None
    size: int | None = None
    note: str = ""
    members: dict[str, Member] = field(default_factory=dict)

    @property
    def is_archive(self) -> bool:
        """Whether members are extracted from this source rather than downloading it."""
        return self.kind == "archive"

    def member(self, key: str) -> Member:
        """Return the pinned member entry for ``key``, or an unpinned placeholder.

        An unknown member is not an error: it simply has nothing pinned yet, which
        the CLI reports rather than treating as a failure.
        """
        return self.members.get(key, Member(key=key, artifact=key))


def sha256_file(path: Path, chunk_size: int = _HASH_CHUNK) -> str:
    """SHA-256 of a file, streamed so that large tiles do not need to fit in memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def verify(path: Path, expected_sha256: str | None) -> bool:
    """Check a downloaded file against its pinned checksum.

    An unpinned artefact (``None``) verifies as ``True`` but is counted and reported
    by the CLI, so "not yet pinned" is visible rather than silent.
    """
    if expected_sha256 is None:
        return True
    return sha256_file(path) == expected_sha256


def verify_or_raise(path: Path, expected_sha256: str | None) -> None:
    """Verify a file, raising :class:`ChecksumMismatchError` when it does not match."""
    if expected_sha256 is None:
        return
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise ChecksumMismatchError(path, expected_sha256, actual)


def _parse_members(entry: dict[str, Any], source_key: str) -> dict[str, Member]:
    """Parse the ``members`` block of one source."""
    members: dict[str, Member] = {}
    for key, raw in (entry.get("members") or {}).items():
        body: dict[str, Any] = raw or {}
        # Coerced to str: an all-digit checksum would otherwise arrive as an int,
        # and one with leading zeros would arrive with them stripped.
        raw_digest = body.get("sha256")
        digest = None if raw_digest is None else str(raw_digest)
        if digest is not None and len(digest) != _SHA256_LENGTH:
            msg = f"{source_key}.members.{key}: sha256 must be 64 hex characters"
            raise ValueError(msg)
        members[key] = Member(
            key=key,
            artifact=body.get("artifact", key),
            sha256=digest,
            size=body.get("size"),
        )
    return members


def load_manifest(path: Path | None = None) -> dict[str, Source]:
    """Load and validate ``data/manifest.yaml``."""
    manifest_path = path or MANIFEST_PATH
    raw: dict[str, Any] = yaml.safe_load(manifest_path.read_text())
    sources: dict[str, Source] = {}
    for key, entry in (raw.get("sources") or {}).items():
        kind = entry.get("kind", "file")
        if kind == "archive" and entry.get("sha256") is not None:
            msg = f"{key}: an archive is never downloaded whole, so it cannot be checksummed"
            raise ValueError(msg)
        sources[key] = Source(
            key=key,
            url=entry["url"],
            kind=kind,
            sha256=entry.get("sha256"),
            size=entry.get("size"),
            note=entry.get("note", ""),
            members=_parse_members(entry, key),
        )
    return sources


def member_yaml_block(members: list[Member], indent: int = 4) -> str:
    """Render members as the YAML block to paste into ``data/manifest.yaml``.

    Used by ``mtl-roofs data checksums``. Emitting text to paste, rather than
    rewriting the file, keeps the manifest's explanatory comments intact and keeps
    pinning an explicit, reviewable decision.
    """
    pad = " " * indent
    lines = [f"{pad}members:"]
    for m in sorted(members, key=lambda x: x.key):
        lines.append(f"{pad}  {m.key}:")
        if m.artifact != m.key:
            lines.append(f"{pad}    artifact: {m.artifact}")
        # Quoted, so YAML cannot reinterpret an all-digit checksum as an integer.
        lines.append(f'{pad}    sha256: "{m.sha256}"')
        lines.append(f"{pad}    size: {m.size}")
    return "\n".join(lines)
