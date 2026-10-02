"""Integration tests. Need `pixi run db-up`; deselected by default in CI."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from mtl_roofs.config import Settings, StudyArea
from mtl_roofs.io.reference import ReferenceBuilding, RoofPolygon

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
TEST_AREA = "it-fixtures"


@pytest.fixture
def conn() -> Iterator[Any]:
    psycopg = pytest.importorskip("psycopg")
    try:
        connection = psycopg.connect(Settings().dsn(), connect_timeout=5, autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostGIS not reachable ({exc}); run `pixi run db-up`")
    with connection:
        yield connection
        from mtl_roofs.io.postgis import AREA_TABLES

        for table in AREA_TABLES:
            if connection.execute("SELECT to_regclass(%s)", (table,)).fetchone()[0]:
                connection.execute(f"DELETE FROM {table} WHERE area = %s", (TEST_AREA,))


def fixture_footprints() -> Any:
    import geopandas as gpd
    import pandas as pd

    frames = [gpd.read_file(p) for p in sorted((FIXTURES / "footprints").glob("*.geojson"))]
    return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=2950)


def fixture_buildings() -> list[ReferenceBuilding]:
    """Every fixture reference building, plus a synthetic ``Groupe*`` block."""
    buildings = []
    for path in sorted((FIXTURES / "reference").glob("*.json")):
        for b in json.loads(path.read_text())["buildings"]:
            roofs = [
                RoofPolygon(f"{b['gml_id']}-{i}", [tuple(p) for p in ring])
                for i, ring in enumerate(b["roof_polygons"])
            ]
            buildings.append(ReferenceBuilding(b["gml_id"], roofs, {"parcelle": "x"}))
    square = [(0.0, 0.0, 50.0), (10.0, 0.0, 50.0), (10.0, 10.0, 50.0), (0.0, 0.0, 50.0)]
    buildings.append(ReferenceBuilding("Groupe1", [RoofPolygon("g-0", square)]))
    return buildings


def count(conn: Any, table: str, area: str = TEST_AREA) -> int:
    row = conn.execute(f"SELECT count(*) FROM {table} WHERE area = %s", (area,)).fetchone()
    return int(row[0])


def test_postgis_extension_is_available(conn: Any) -> None:
    """The database container must expose PostGIS, not just PostgreSQL."""
    assert conn.execute("SELECT postgis_version()").fetchone() is not None


def test_load_writes_every_row(conn: Any) -> None:
    from mtl_roofs.io.postgis import load_area

    footprints, buildings = fixture_footprints(), fixture_buildings()
    counts = load_area(conn, TEST_AREA, footprints, buildings)

    assert counts.footprints == count(conn, "footprints") == 20
    assert counts.reference_buildings == count(conn, "reference_buildings") == len(buildings)
    assert counts.roof_surfaces == count(conn, "reference_roof_surfaces")
    assert counts.roof_surfaces == sum(len(b.roofs) for b in buildings)


def test_reload_replaces_rather_than_duplicates(conn: Any) -> None:
    from mtl_roofs.io.postgis import load_area

    footprints, buildings = fixture_footprints(), fixture_buildings()
    load_area(conn, TEST_AREA, footprints, buildings)
    load_area(conn, TEST_AREA, footprints.iloc[:5], buildings[:3])

    assert count(conn, "footprints") == 5
    assert count(conn, "reference_buildings") == 3


def test_every_geometry_column_is_2950_with_a_gist_index(conn: Any) -> None:
    from mtl_roofs.io.postgis import init_schema

    init_schema(conn)
    columns = conn.execute(
        "SELECT f_table_name, f_geometry_column, srid FROM geometry_columns"
        " WHERE f_table_name = ANY(%s)",
        (["footprints", "reference_buildings", "reference_roof_surfaces"],),
    ).fetchall()
    assert len(columns) == 3
    assert {srid for _, _, srid in columns} == {2950}
    for table, column, _ in columns:
        indexed = conn.execute(
            "SELECT count(*) FROM pg_indexes WHERE tablename = %s AND indexdef ILIKE %s",
            (table, f"%USING gist ({column})%"),
        ).fetchone()
        assert indexed[0] == 1, f"{table}.{column} has no GiST index"


def test_provenance_and_groupe_flag_survive(conn: Any) -> None:
    from mtl_roofs.io.postgis import load_area

    footprints = fixture_footprints()
    load_area(conn, TEST_AREA, footprints, fixture_buildings())

    lidar = conn.execute(
        "SELECT count(*) FROM footprints WHERE area = %s AND lidar_derived", (TEST_AREA,)
    ).fetchone()[0]
    assert lidar == int(footprints["lidar_derived"].sum())
    grouped = conn.execute(
        "SELECT gml_id FROM reference_buildings WHERE area = %s AND is_grouped", (TEST_AREA,)
    ).fetchall()
    assert grouped == [("Groupe1",)]


def test_reference_outlines_sit_inside_their_footprints(conn: Any) -> None:
    """A wrong axis order or CRS would put the reference nowhere near the footprints."""
    from mtl_roofs.io.postgis import load_area

    load_area(conn, TEST_AREA, fixture_footprints(), fixture_buildings())
    outside = conn.execute(
        "SELECT count(*) FROM reference_buildings r WHERE r.area = %(a)s"
        " AND NOT r.is_grouped AND NOT EXISTS (SELECT 1 FROM footprints f"
        " WHERE f.area = %(a)s AND ST_Intersects(f.geom, r.outline))",
        {"a": TEST_AREA},
    ).fetchone()[0]
    assert outside == 0


def test_real_area_loads_the_expected_building_count(conn: Any) -> None:
    """#14: loading cdn-ndg-03 from data/raw gives the area's 1109 reference buildings."""
    from mtl_roofs.io.footprints import load_footprints
    from mtl_roofs.io.postgis import load_area
    from mtl_roofs.io.reference import iter_buildings

    study = StudyArea.load("cdn-ndg-03")
    raw = Settings().raw_dir / study.name
    zip_path = raw / "batiments_2d_2016_arrondissements.zip"
    gmls = [raw / f"{t}_2016.gml" for t in study.reference_tiles]
    if not zip_path.exists() or not all(g.exists() for g in gmls):
        pytest.skip("run `mtl-roofs data fetch -a cdn-ndg-03` first")

    b = study.bbox
    footprints = load_footprints(zip_path, bbox=(b.xmin, b.ymin, b.xmax, b.ymax))
    buildings = (bld for g in gmls for bld in iter_buildings(g))
    counts = load_area(conn, TEST_AREA, footprints, buildings)

    assert counts.reference_buildings == study.expected_buildings == 1109
    assert count(conn, "reference_buildings") == 1109
    assert count(conn, "footprints") == counts.footprints > 0
