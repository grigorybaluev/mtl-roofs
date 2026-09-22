"""3D Tiles export.

Data products are built locally and attached to GitHub Releases; CI never runs the
full pipeline. The viewer workflow downloads the tileset asset from the latest
release at deploy time.
"""

from __future__ import annotations


def write_tileset(*_args: object, **_kwargs: object) -> None:
    """Write a 3D Tiles tileset for a study area.

    Not implemented yet: tracked by the "3D Tiles export" issue.
    """
    raise NotImplementedError
