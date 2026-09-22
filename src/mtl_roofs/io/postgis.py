"""PostGIS access for footprints, reconstructions and per-building results."""

from __future__ import annotations


def get_engine(*_args: object, **_kwargs: object) -> object:
    """Create a SQLAlchemy engine against the configured database.

    Not implemented yet: tracked by the "Reprojection and PostGIS loading" issue.
    """
    raise NotImplementedError


def init_schema(*_args: object, **_kwargs: object) -> None:
    """Create the PostGIS schema and enable the postgis extension.

    Not implemented yet: tracked by the "Reprojection and PostGIS loading" issue.
    """
    raise NotImplementedError
