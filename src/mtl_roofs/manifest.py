"""The committed data manifest: what to download, from where, and its checksum.

``data/manifest.yaml`` is the single source of truth for every byte this project
ingests. Nothing is fetched from a URL that is not pinned there.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from mtl_roofs.config import REPO_ROOT

MANIFEST_PATH = REPO_ROOT / "data" / "manifest.yaml"

#: Read in this many bytes at a time when checksumming a multi-hundred-MB file.
_HASH_CHUNK = 1 << 20


@dataclass(frozen=True, slots=True)
class Source:
    """One pinned source: either a whole file or a member of a remote archive."""

    key: str
    url: str
    kind: str
    sha256: str | None = None
    size: int | None = None
    member: str | None = None
    note: str = ""

    @property
    def is_archive_member(self) -> bool:
        """Whether this source is extracted from inside a remote archive."""
        return self.member is not None


def sha256_file(path: Path, chunk_size: int = _HASH_CHUNK) -> str:
    """SHA-256 of a file, streamed so that large tiles do not need to fit in memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def verify(path: Path, expected_sha256: str | None) -> bool:
    """Check a downloaded file against its pinned checksum.

    A source whose checksum is not yet pinned (``None``) verifies as ``True`` but is
    reported by the CLI, so an unpinned source is visible rather than silent.
    """
    if expected_sha256 is None:
        return True
    return sha256_file(path) == expected_sha256


def load_manifest(path: Path | None = None) -> dict[str, Source]:
    """Load and validate ``data/manifest.yaml``."""
    manifest_path = path or MANIFEST_PATH
    raw: dict[str, Any] = yaml.safe_load(manifest_path.read_text())
    sources: dict[str, Source] = {}
    for key, entry in (raw.get("sources") or {}).items():
        sources[key] = Source(
            key=key,
            url=entry["url"],
            kind=entry.get("kind", "file"),
            sha256=entry.get("sha256"),
            size=entry.get("size"),
            member=entry.get("member"),
            note=entry.get("note", ""),
        )
    return sources
