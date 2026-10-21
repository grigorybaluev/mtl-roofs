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
TABLES_WITH_GEOMETRY = [
    "footprints",
    "reference_buildings",
    "reference_roof_surfaces",
    "reconstructions",
    "roof_faces",
]


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


def test_degenerate_rings_are_skipped_not_fatal(conn: Any) -> None:
    """A roof ring with fewer than four vertices cannot be a closed polygon; Verdun's
    model has a few. The load skips them instead of failing on the whole area."""
    from mtl_roofs.io.postgis import load_area

    buildings = fixture_buildings()
    two_points = [(0.0, 0.0, 50.0), (5.0, 0.0, 50.0)]
    buildings[0].roofs.append(RoofPolygon("degenerate-0", two_points))
    counts = load_area(conn, TEST_AREA, fixture_footprints(), buildings)

    valid = sum(1 for b in buildings for r in b.roofs if len(r.points) >= 4)
    assert counts.roof_surfaces == count(conn, "reference_roof_surfaces") == valid


def test_tile_local_groupe_ids_load_side_by_side(conn: Any) -> None:
    """Two tiles each numbering a ``Groupe1`` no longer collide on (area, gml_id)."""
    from mtl_roofs.io.postgis import load_area
    from mtl_roofs.io.reference import qualify_grouped

    square = [(0.0, 0.0, 50.0), (10.0, 0.0, 50.0), (10.0, 10.0, 50.0), (0.0, 0.0, 50.0)]
    tiles = [
        b
        for tile in ("T01", "T02")
        for b in qualify_grouped([ReferenceBuilding("Groupe1", [RoofPolygon("g", square)])], tile)
    ]
    load_area(conn, TEST_AREA, fixture_footprints(), tiles)
    ids = conn.execute(
        "SELECT gml_id FROM reference_buildings WHERE area = %s AND is_grouped ORDER BY 1",
        (TEST_AREA,),
    ).fetchall()
    assert ids == [("T01:Groupe1",), ("T02:Groupe1",)]


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
        (TABLES_WITH_GEOMETRY,),
    ).fetchall()
    assert len(columns) == len(TABLES_WITH_GEOMETRY)
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


def test_a_reconstruction_round_trips_as_a_closed_solid(conn: Any) -> None:
    """#21: a saved solid is a closed polyhedral surface to PostGIS too, and re-saving
    a footprint replaces its row and faces."""
    from mtl_roofs.batch import footprint_points
    from mtl_roofs.io.lidar import PointIndex, read_points
    from mtl_roofs.io.postgis import (
        area_footprints,
        completed_footprints,
        load_area,
        save_reconstruction,
    )
    from mtl_roofs.pipeline import reconstruct_footprint

    load_area(conn, TEST_AREA, fixture_footprints(), fixture_buildings())
    # ST_IsClosed reads only each surface's exterior ring, so it calls a solid open
    # when a roof face has a hole (a chimney face inside a flat roof). Our own
    # edge-parity check counts every ring; here a footprint without holes is used.
    fid = "fp-2926302-50356572"
    polygon = {f: p for f, _, p in area_footprints(conn, TEST_AREA)}[fid]
    index = PointIndex.build(read_points(FIXTURES / "points" / f"{fid}.laz"))
    result = reconstruct_footprint(footprint_points(index, polygon), polygon)
    for _ in range(2):
        save_reconstruction(conn, TEST_AREA, fid, result, 1.0, "test-run")

    assert completed_footprints(conn, TEST_AREA) == {fid}
    status, closed, n_faces, volume = conn.execute(
        "SELECT status, ST_IsClosed(solid), n_faces, volume_m3 FROM reconstructions"
        " WHERE area = %s AND footprint_id = %s",
        (TEST_AREA, fid),
    ).fetchone()
    assert status == "ok"
    assert closed
    assert n_faces == len(result.faces) == count(conn, "roof_faces")
    assert volume == pytest.approx(result.extrusion.solid.volume())

    # Reloading the inputs drops the reconstructions made from them.
    load_area(conn, TEST_AREA, fixture_footprints(), fixture_buildings())
    assert count(conn, "reconstructions") == count(conn, "roof_faces") == 0


def test_the_batch_connection_commits_each_footprint_as_it_is_saved(conn: Any) -> None:
    """#21: a run that dies half way keeps what it saved, so a re-run resumes."""
    from mtl_roofs.io.postgis import connect, load_area, save_reconstruction
    from mtl_roofs.pipeline import Reconstruction, Status

    load_area(conn, TEST_AREA, fixture_footprints(), fixture_buildings())
    fid = "fp-2926302-50356572"
    with connect(autocommit=True) as batch:
        save_reconstruction(batch, TEST_AREA, fid, Reconstruction(Status.NO_PLANES), 0.1, "r")
        # Still open: another connection must already see the row.
        assert count(conn, "reconstructions") == 1


def test_the_area_evaluation_reads_back_what_the_fixtures_score(conn: Any) -> None:
    """#26: footprints, faces and reference read from PostGIS score as the fixtures do,
    and the stored rows read back unchanged."""
    from mtl_roofs.batch import footprint_points
    from mtl_roofs.evaluation.evaluate import evaluate
    from mtl_roofs.io.lidar import PointIndex, read_points
    from mtl_roofs.io.postgis import (
        area_buildings,
        area_outcomes,
        load_area,
        load_building_results,
        save_building_results,
        save_reconstruction,
    )
    from mtl_roofs.pipeline import reconstruct_footprint

    load_area(conn, TEST_AREA, fixture_footprints(), fixture_buildings())
    fid = "fp-2926302-50356572"
    polygon = {o.footprint_id: o.polygon for o in area_outcomes(conn, TEST_AREA)}[fid]
    index = PointIndex.build(read_points(FIXTURES / "points" / f"{fid}.laz"))
    result = reconstruct_footprint(footprint_points(index, polygon), polygon)
    save_reconstruction(conn, TEST_AREA, fid, result, 1.0, "test-run")

    outcomes = area_outcomes(conn, TEST_AREA)
    assert {o.status for o in outcomes} == {"ok", "not-run"}
    done = [o for o in outcomes if o.footprint_id == fid]
    assert len(done[0].faces) == len(result.faces)

    buildings = area_buildings(conn, TEST_AREA)
    assert len(buildings) == len(fixture_buildings())
    rows = evaluate(done, [b for b in buildings if not b.is_grouped])
    scored = [r for r in rows if r.footprint_id == fid and r.status == "ok"]
    assert len(scored) == 4
    assert all(r.rmse_m is not None for r in scored)

    save_building_results(conn, TEST_AREA, rows)
    assert load_building_results(conn, TEST_AREA) == sorted(rows, key=lambda r: r.building_id)
