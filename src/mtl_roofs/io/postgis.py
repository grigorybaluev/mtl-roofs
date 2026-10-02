"""PostGIS store for a study area's footprints and reference model (#14).

Downstream stages query these tables instead of re-reading a 100 MB shapefile archive
and 87 MB of CityGML per tile. Three tables, every geometry column in EPSG:2950 with a
GiST index:

* ``footprints``: ``CARTO-BAT-TOIT`` roof outlines, keyed by the project's
  ``footprint_id`` (ADR 0005), with the layer's per-feature provenance;
* ``reference_buildings``: CityGML buildings with their 2D roof outline, ``Groupe*``
  blocks flagged rather than dropped;
* ``reference_roof_surfaces``: each 3D roof polygon of each reference building.

Every row carries the study area it was loaded for. Loading an area replaces that
area's rows in one transaction, so re-running never duplicates and a failed load
leaves the previous one in place.

Nothing here assigns reference buildings to footprints. That mapping is produced by
the matching stage and stored by it (ADR 0005, #24).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import psycopg
import shapely

from mtl_roofs import CRS_EPSG
from mtl_roofs.config import Settings
from mtl_roofs.io.reference import ReferenceBuilding, roof_outline

#: Idempotent DDL. Tables are only ever created here, never altered in place.
SCHEMA_SQL = f"""
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS footprints (
    area          text     NOT NULL,
    footprint_id  text     NOT NULL,
    lidar_derived boolean  NOT NULL,
    methode       text     NOT NULL,
    source        text     NOT NULL,
    maj           text     NOT NULL,
    geom          geometry(Polygon, {CRS_EPSG}) NOT NULL,
    PRIMARY KEY (area, footprint_id)
);
CREATE INDEX IF NOT EXISTS footprints_geom_gist ON footprints USING gist (geom);

CREATE TABLE IF NOT EXISTS reference_buildings (
    area            text    NOT NULL,
    gml_id          text    NOT NULL,
    is_grouped      boolean NOT NULL,
    parcelle        text,
    n_roof_surfaces integer NOT NULL,
    roof_area_m2    double precision NOT NULL,
    outline         geometry(MultiPolygon, {CRS_EPSG}),
    PRIMARY KEY (area, gml_id)
);
CREATE INDEX IF NOT EXISTS reference_buildings_outline_gist
    ON reference_buildings USING gist (outline);

CREATE TABLE IF NOT EXISTS reference_roof_surfaces (
    area       text    NOT NULL,
    gml_id     text    NOT NULL,
    ordinal    integer NOT NULL,
    surface_id text    NOT NULL,
    area_m2    double precision NOT NULL,
    slope_deg  double precision NOT NULL,
    geom       geometry(PolygonZ, {CRS_EPSG}) NOT NULL,
    PRIMARY KEY (area, gml_id, ordinal),
    FOREIGN KEY (area, gml_id) REFERENCES reference_buildings ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS reference_roof_surfaces_geom_gist
    ON reference_roof_surfaces USING gist (geom);
"""

#: Tables holding per-area rows, children before parents so a delete never orphans.
AREA_TABLES = ("reference_roof_surfaces", "reference_buildings", "footprints")


@dataclass(frozen=True, slots=True)
class LoadCounts:
    """Rows written for one study area."""

    footprints: int
    lidar_derived_footprints: int
    reference_buildings: int
    grouped_buildings: int
    roof_surfaces: int


def connect(settings: Settings | None = None) -> psycopg.Connection[tuple[Any, ...]]:
    """Open a connection to the configured database."""
    return psycopg.connect((settings or Settings()).dsn(), connect_timeout=10)


def init_schema(conn: psycopg.Connection[Any]) -> None:
    """Create the tables and their GiST indexes if they do not exist yet."""
    conn.execute(SCHEMA_SQL)


def load_area(
    conn: psycopg.Connection[Any],
    area: str,
    footprints: Any,  # geopandas.GeoDataFrame from io.footprints.load_footprints
    buildings: Iterable[ReferenceBuilding],
) -> LoadCounts:
    """Replace one study area's rows with ``footprints`` and ``buildings``.

    Runs in a single transaction: either the whole area is replaced or nothing changes.

    Args:
        conn: An open connection; the schema is created if missing.
        area: Study area name, stored on every row.
        footprints: Output of :func:`mtl_roofs.io.footprints.load_footprints`.
        buildings: Reference buildings, typically :func:`~mtl_roofs.io.reference.iter_buildings`.
            The CityGML declares no CRS; coordinates are taken as EPSG:2950.
    """
    with conn.transaction():
        init_schema(conn)
        for table in AREA_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE area = %s", (area,))

        with conn.cursor() as cur:
            with cur.copy(
                "COPY footprints (area, footprint_id, lidar_derived, methode, source, maj, geom)"
                " FROM STDIN"
            ) as copy:
                for row in footprints.itertuples(index=False):
                    copy.write_row(
                        (
                            area,
                            row.footprint_id,
                            bool(row.lidar_derived),
                            row.methode,
                            row.source,
                            row.MAJ,
                            _ewkb(row.geometry),
                        )
                    )

            n_buildings = n_grouped = n_surfaces = 0
            surfaces: list[tuple[object, ...]] = []
            with cur.copy(
                "COPY reference_buildings (area, gml_id, is_grouped, parcelle,"
                " n_roof_surfaces, roof_area_m2, outline) FROM STDIN"
            ) as copy:
                for building in buildings:
                    outline = roof_outline(building)
                    copy.write_row(
                        (
                            area,
                            building.gml_id,
                            building.is_grouped,
                            building.attributes.get("parcelle"),
                            len(building.roofs),
                            building.roof_area(),
                            None if outline.is_empty else _ewkb(_multi(outline)),
                        )
                    )
                    n_buildings += 1
                    n_grouped += building.is_grouped
                    for ordinal, roof in enumerate(building.roofs):
                        surfaces.append(
                            (
                                area,
                                building.gml_id,
                                ordinal,
                                roof.gml_id,
                                roof.area(),
                                roof.slope_degrees(),
                                _ewkb(shapely.Polygon(roof.points)),
                            )
                        )
            with cur.copy(
                "COPY reference_roof_surfaces (area, gml_id, ordinal, surface_id,"
                " area_m2, slope_deg, geom) FROM STDIN"
            ) as copy:
                for surface in surfaces:
                    copy.write_row(surface)
                n_surfaces = len(surfaces)

    return LoadCounts(
        footprints=len(footprints),
        lidar_derived_footprints=int(footprints["lidar_derived"].sum()),
        reference_buildings=n_buildings,
        grouped_buildings=n_grouped,
        roof_surfaces=n_surfaces,
    )


def _ewkb(geom: Any) -> str:
    """Hex EWKB with the SRID asserted as EPSG:2950, as COPY accepts for geometry."""
    return str(shapely.to_wkb(shapely.set_srid(geom, CRS_EPSG), hex=True, include_srid=True))


def _multi(geom: Any) -> Any:
    """Wrap a single Polygon as a MultiPolygon so every outline has the column's type."""
    if isinstance(geom, shapely.Polygon):
        return shapely.MultiPolygon([geom])
    return geom
