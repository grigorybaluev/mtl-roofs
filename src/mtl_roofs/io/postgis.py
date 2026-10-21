"""PostGIS store for a study area's footprints and reference model (#14).

Downstream stages query these tables instead of re-reading a 100 MB shapefile archive
and 87 MB of CityGML per tile. Three input tables, every geometry column in EPSG:2950
with a GiST index:

* ``footprints``: ``CARTO-BAT-TOIT`` roof outlines, keyed by the project's
  ``footprint_id`` (ADR 0005), with the layer's per-feature provenance;
* ``reference_buildings``: CityGML buildings with their 2D roof outline, ``Groupe*``
  blocks flagged rather than dropped;
* ``reference_roof_surfaces``: each 3D roof polygon of each reference building.

and two written by the batch reconstruction (#21):

* ``reconstructions``: one row per footprint with its failure-log status, the
  solid as a ``POLYHEDRALSURFACE Z`` and the semantic kind of each of its surfaces;
* ``roof_faces``: the roof partition, one 2D polygon per face with its plane.

and one written by the evaluation (#24, #26):

* ``building_results``: one row per reference building, and per footprint with none
  assigned, with its status, its assignment (footprint and share; the stored
  mapping ADR 0005 asks for) and its metrics.

Every row carries the study area it was loaded for. Loading an area replaces that
area's rows in one transaction, so re-running never duplicates and a failed load
leaves the previous one in place. It also drops the area's reconstructions, which
were made from the replaced inputs.

Nothing here assigns reference buildings to footprints. That mapping is produced by
the evaluation and stored in ``building_results`` (ADR 0005, #24).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
import psycopg
import shapely

from mtl_roofs import CRS_EPSG
from mtl_roofs.config import Settings
from mtl_roofs.evaluation.report import BuildingResult
from mtl_roofs.io.reference import ReferenceBuilding, roof_outline

if TYPE_CHECKING:
    from mtl_roofs.evaluation.evaluate import FootprintOutcome
    from mtl_roofs.geometry.extrude import Solid
    from mtl_roofs.pipeline import Reconstruction

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

CREATE TABLE IF NOT EXISTS reconstructions (
    area          text     NOT NULL,
    footprint_id  text     NOT NULL,
    status        text     NOT NULL,
    reason        text     NOT NULL,
    warnings      text[]   NOT NULL,
    n_points      integer  NOT NULL,
    density       double precision NOT NULL,
    ground_z      double precision,
    n_planes      integer  NOT NULL,
    n_faces       integer  NOT NULL,
    volume_m3     double precision,
    seconds       double precision NOT NULL,
    run_id        text     NOT NULL,
    surface_kinds text[],
    solid         geometry(PolyhedralSurfaceZ, {CRS_EPSG}),
    PRIMARY KEY (area, footprint_id),
    FOREIGN KEY (area, footprint_id) REFERENCES footprints ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS reconstructions_solid_gist ON reconstructions USING gist (solid);

CREATE TABLE IF NOT EXISTS roof_faces (
    area         text    NOT NULL,
    footprint_id text    NOT NULL,
    face         integer NOT NULL,
    plane_index  integer NOT NULL,
    nx           double precision NOT NULL,
    ny           double precision NOT NULL,
    nz           double precision NOT NULL,
    d            double precision NOT NULL,
    geom         geometry(Polygon, {CRS_EPSG}) NOT NULL,
    PRIMARY KEY (area, footprint_id, face),
    FOREIGN KEY (area, footprint_id) REFERENCES reconstructions ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS roof_faces_geom_gist ON roof_faces USING gist (geom);

CREATE TABLE IF NOT EXISTS building_results (
    area                   text    NOT NULL,
    building_id            text    NOT NULL,
    footprint_id           text,
    status                 text    NOT NULL,
    roof_type              text,
    outline_area_m2        double precision NOT NULL,
    share                  double precision,
    point_density          double precision,
    lidar_derived          boolean,
    n_ref_cells            integer NOT NULL,
    n_valid_cells          integer NOT NULL,
    sum_sq_m2              double precision NOT NULL,
    rmse_m                 double precision,
    coverage               double precision,
    temporal_offset_m      double precision,
    orientation_errors_deg double precision[] NOT NULL,
    orientation_median_deg double precision,
    n_pred_planes          integer,
    n_ref_planes           integer,
    n_unmatched_pred       integer,
    n_unmatched_ref        integer,
    n_pred_faces           integer,
    n_ref_faces            integer,
    face_count_agreement   double precision,
    reason                 text    NOT NULL,
    warnings               text[]  NOT NULL,
    PRIMARY KEY (area, building_id)
);
"""

#: Tables holding per-area rows, children before parents so a delete never orphans.
#: Reloading an area's inputs also drops its reconstructions, which would be stale.
AREA_TABLES = (
    "building_results",
    "roof_faces",
    "reconstructions",
    "reference_roof_surfaces",
    "reference_buildings",
    "footprints",
)

#: Columns of ``reconstructions`` that make up its CSV export, in order.
RECONSTRUCTION_COLUMNS = (
    "footprint_id",
    "status",
    "reason",
    "warnings",
    "n_points",
    "density",
    "ground_z",
    "n_planes",
    "n_faces",
    "volume_m3",
    "seconds",
    "run_id",
)


@dataclass(frozen=True, slots=True)
class LoadCounts:
    """Rows written for one study area."""

    footprints: int
    lidar_derived_footprints: int
    reference_buildings: int
    grouped_buildings: int
    roof_surfaces: int


def connect(
    settings: Settings | None = None, autocommit: bool = False
) -> psycopg.Connection[tuple[Any, ...]]:
    """Open a connection to the configured database.

    Args:
        settings: Defaults to :class:`~mtl_roofs.config.Settings`.
        autocommit: Make each ``conn.transaction()`` block a transaction of its own.
            Without it, the first statement opens a transaction that lasts until the
            connection closes, and the blocks inside it are only savepoints: a long
            batch would then commit nothing until it ended.
    """
    return psycopg.connect(
        (settings or Settings()).dsn(), connect_timeout=10, autocommit=autocommit
    )


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
                        if len(roof.points) < 4:
                            # A closed ring needs four vertices; Verdun's model has a few
                            # that do not (same rule as roof_outline). Skipped, not guessed;
                            # the ordinal gap shows where one was.
                            continue
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


def area_footprints(conn: psycopg.Connection[Any], area: str) -> list[tuple[str, bool, Any]]:
    """An area's footprints as ``(footprint_id, lidar_derived, polygon)``, by id."""
    rows = conn.execute(
        "SELECT footprint_id, lidar_derived, ST_AsBinary(geom) FROM footprints"
        " WHERE area = %s ORDER BY footprint_id",
        (area,),
    ).fetchall()
    return [(fid, bool(derived), shapely.from_wkb(bytes(wkb))) for fid, derived, wkb in rows]


def completed_footprints(conn: psycopg.Connection[Any], area: str) -> set[str]:
    """Footprints of an area that already have a reconstruction row."""
    rows = conn.execute(
        "SELECT footprint_id FROM reconstructions WHERE area = %s", (area,)
    ).fetchall()
    return {fid for (fid,) in rows}


def clear_reconstructions(conn: psycopg.Connection[Any], area: str) -> None:
    """Delete an area's reconstructions (and, by cascade, their roof faces)."""
    with conn.transaction():
        init_schema(conn)
        conn.execute("DELETE FROM reconstructions WHERE area = %s", (area,))


def save_reconstruction(
    conn: psycopg.Connection[Any],
    area: str,
    footprint_id: str,
    result: Reconstruction,
    seconds: float,
    run_id: str,
) -> None:
    """Write one footprint's reconstruction, replacing any earlier one, in one transaction."""
    solid = result.extrusion.solid if result.extrusion is not None else None
    n_planes = len(result.topology.planes) if result.topology is not None else 0
    with conn.transaction():
        conn.execute(
            "DELETE FROM reconstructions WHERE area = %s AND footprint_id = %s",
            (area, footprint_id),
        )
        conn.execute(
            "INSERT INTO reconstructions (area, footprint_id, status, reason, warnings,"
            " n_points, density, ground_z, n_planes, n_faces, volume_m3, seconds, run_id,"
            " surface_kinds, solid) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,"
            " %s, %s, %s, ST_GeomFromText(%s, %s))",
            (
                area,
                footprint_id,
                result.status.value,
                result.reason,
                list(result.warnings),
                result.n_points,
                result.density,
                None if np.isnan(result.ground_z) else result.ground_z,
                n_planes,
                len(result.faces),
                solid.volume() if solid is not None else None,
                seconds,
                run_id,
                [surface.kind.value for surface in solid.surfaces] if solid else None,
                _polyhedral_wkt(solid) if solid is not None else None,
                CRS_EPSG,
            ),
        )
        with (
            conn.cursor() as cur,
            cur.copy(
                "COPY roof_faces (area, footprint_id, face, plane_index, nx, ny, nz, d, geom)"
                " FROM STDIN"
            ) as copy,
        ):
            for i, face in enumerate(result.faces):
                nx, ny, nz = face.plane.normal
                copy.write_row(
                    (
                        area,
                        footprint_id,
                        i,
                        face.plane_index,
                        nx,
                        ny,
                        nz,
                        face.plane.offset,
                        _ewkb(face.polygon),
                    )
                )


def reconstruction_rows(conn: psycopg.Connection[Any], area: str) -> list[tuple[Any, ...]]:
    """An area's reconstruction rows, :data:`RECONSTRUCTION_COLUMNS` in order, by id."""
    return conn.execute(
        f"SELECT {', '.join(RECONSTRUCTION_COLUMNS)} FROM reconstructions"
        " WHERE area = %s ORDER BY footprint_id",
        (area,),
    ).fetchall()


def area_outcomes(conn: psycopg.Connection[Any], area: str) -> list[FootprintOutcome]:
    """Every footprint of an area with its reconstruction, as the evaluation reads them.

    A footprint with no reconstruction row has not been processed; it is returned with
    status ``not-run`` so that the evaluation still lists it rather than drop it.
    """
    from mtl_roofs.evaluation.evaluate import FootprintOutcome
    from mtl_roofs.geometry.planes import Plane
    from mtl_roofs.geometry.roof import RoofFace

    faces: dict[str, list[RoofFace]] = {}
    for fid, plane_index, nx, ny, nz, d, wkb in conn.execute(
        "SELECT footprint_id, plane_index, nx, ny, nz, d, ST_AsBinary(geom) FROM roof_faces"
        " WHERE area = %s ORDER BY footprint_id, face",
        (area,),
    ):
        plane = Plane((nx, ny, nz), d)
        faces.setdefault(fid, []).append(RoofFace(plane_index, plane, shapely.from_wkb(bytes(wkb))))

    rows = conn.execute(
        "SELECT f.footprint_id, f.lidar_derived, ST_AsBinary(f.geom), r.status, r.reason,"
        " r.warnings, r.density FROM footprints f LEFT JOIN reconstructions r"
        " USING (area, footprint_id) WHERE f.area = %s ORDER BY f.footprint_id",
        (area,),
    ).fetchall()
    return [
        FootprintOutcome(
            footprint_id=fid,
            polygon=shapely.from_wkb(bytes(wkb)),
            status=status or "not-run",
            faces=faces.get(fid, []),
            density=density or 0.0,
            lidar_derived=bool(derived),
            reason=reason or "",
            warnings=tuple(warnings or ()),
        )
        for fid, derived, wkb, status, reason, warnings, density in rows
    ]


def area_buildings(conn: psycopg.Connection[Any], area: str) -> list[ReferenceBuilding]:
    """An area's reference buildings with their 3D roof polygons, by ``gml:id``."""
    from mtl_roofs.io.reference import RoofPolygon

    roofs: dict[str, list[RoofPolygon]] = {}
    for gml_id, surface_id, wkb in conn.execute(
        "SELECT gml_id, surface_id, ST_AsBinary(geom) FROM reference_roof_surfaces"
        " WHERE area = %s ORDER BY gml_id, ordinal",
        (area,),
    ):
        ring = shapely.from_wkb(bytes(wkb)).exterior.coords
        roofs.setdefault(gml_id, []).append(
            RoofPolygon(surface_id, [(x, y, z) for x, y, z in ring])
        )
    ids = conn.execute(
        "SELECT gml_id FROM reference_buildings WHERE area = %s ORDER BY gml_id", (area,)
    ).fetchall()
    return [ReferenceBuilding(gml_id, roofs.get(gml_id, [])) for (gml_id,) in ids]


def save_building_results(
    conn: psycopg.Connection[Any], area: str, rows: Iterable[BuildingResult]
) -> int:
    """Replace an area's per-building evaluation rows; returns how many were written."""
    names = [f.name for f in dataclasses.fields(BuildingResult)]
    n = 0
    with conn.transaction():
        init_schema(conn)
        conn.execute("DELETE FROM building_results WHERE area = %s", (area,))
        with (
            conn.cursor() as cur,
            cur.copy(f"COPY building_results (area, {', '.join(names)}) FROM STDIN") as copy,
        ):
            for row in rows:
                record = dataclasses.asdict(row)
                record["orientation_errors_deg"] = list(row.orientation_errors_deg)
                record["warnings"] = list(row.warnings)
                copy.write_row((area, *(record[name] for name in names)))
                n += 1
    return n


def load_building_results(conn: psycopg.Connection[Any], area: str) -> list[BuildingResult]:
    """An area's per-building evaluation rows, as :func:`save_building_results` wrote them."""
    names = [f.name for f in dataclasses.fields(BuildingResult)]
    rows = conn.execute(
        f"SELECT {', '.join(names)} FROM building_results WHERE area = %s ORDER BY building_id",
        (area,),
    ).fetchall()
    results = []
    for row in rows:
        record = dict(zip(names, row, strict=True))
        record["orientation_errors_deg"] = tuple(record["orientation_errors_deg"] or ())
        record["warnings"] = tuple(record["warnings"] or ())
        results.append(BuildingResult(**record))
    return results


def _polyhedral_wkt(solid: Solid) -> str:
    """A solid as ``POLYHEDRALSURFACE Z`` WKT, each ring closed, 0.1 mm precision."""

    def ring(ids: list[int]) -> str:
        pts = solid.vertices[[*ids, ids[0]]]
        return "(" + ", ".join(f"{x:.4f} {y:.4f} {z:.4f}" for x, y, z in pts) + ")"

    surfaces = ("(" + ", ".join(ring(r) for r in s.rings) + ")" for s in solid.surfaces)
    return "POLYHEDRALSURFACE Z (" + ", ".join(surfaces) + ")"


def _ewkb(geom: Any) -> str:
    """Hex EWKB with the SRID asserted as EPSG:2950, as COPY accepts for geometry."""
    return str(shapely.to_wkb(shapely.set_srid(geom, CRS_EPSG), hex=True, include_srid=True))


def _multi(geom: Any) -> Any:
    """Wrap a single Polygon as a MultiPolygon so every outline has the column's type."""
    if isinstance(geom, shapely.Polygon):
        return shapely.MultiPolygon([geom])
    return geom
