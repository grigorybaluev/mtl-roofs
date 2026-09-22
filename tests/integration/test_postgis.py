"""Integration tests. Need `pixi run db-up`; deselected by default in CI."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_postgis_extension_is_available() -> None:
    """The database container must expose PostGIS, not just PostgreSQL."""
    psycopg = pytest.importorskip("psycopg")
    from mtl_roofs.config import Settings

    with psycopg.connect(Settings().dsn(), connect_timeout=5) as conn:
        version = conn.execute("SELECT postgis_version()").fetchone()
    assert version is not None
