"""Per-building form from the reference model, as one Parquet file per study area (#91).

For consumers that need each building's measured height and footprint without running
the reconstruction. One row per reference building:

========================  ==============================================================
``building_id``           ``<tile>:<gml:id>``. Tile-qualified because ``Groupe<n>`` ids
                          restart in every tile: in Verdun, 88 ids repeat across tiles
                          and 87 of them are different buildings.
``tile``, ``gml_id``      the two halves, kept separately
``is_grouped``            a merged ``Groupe*`` block rather than one addressed building
``parcelle``              the generic attribute, ``None`` when blank (all of Verdun)
``outline``               2D union of the roof surfaces, EPSG:2950 (GeoParquet, WKB)
``footprint_area_m2``     area of ``outline``. Roofs are photogrammetric, so eaves are
                          included and this slightly overstates the wall footprint
``ground_z``              ground elevation, CGVD28, by ``ground_method``
``ground_method``         ``lod2_minus3``: lowest solid vertex + 3 m, because the walls
                          are extruded to 3 m below grade (publisher's method note,
                          checked against LiDAR in ``docs/verdun-reference-checks.md``)
``roof_z_max``            highest roof vertex
``roof_z_p50``            area-weighted median of the roof surfaces' mean heights
``height_max_m``          ``roof_z_max - ground_z``
``height_p50_m``          ``roof_z_p50 - ground_z``
``roof_form``             ``flat`` / ``mixed`` / ``pitched``, the evaluation's own
                          :meth:`~mtl_roofs.io.reference.ReferenceBuilding.roof_type`
``n_roof_surfaces``       roof polygons in the model
``model_vintage``         2016, the reference year of the model
``source_sha256``         SHA-256 of the tile's ``.gml`` as read
``mtl_roofs_version``     the package version that wrote the row
========================  ==============================================================

A building with no roof surfaces is kept, with its geometry and heights ``None``, so a
consumer can count it rather than never learn it existed.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from mtl_roofs import CRS_EPSG, __version__
from mtl_roofs.io.reference import ReferenceBuilding, iter_buildings, roof_outline, tile_of
from mtl_roofs.manifest import sha256_file

#: Reference year of the city's LOD2 model; the files were written in 2018.
MODEL_VINTAGE = 2016

#: Depth below grade the publisher extrudes walls to.
WALL_EXTRUSION_BELOW_GRADE_M = 3.0

GROUND_METHOD = "lod2_minus3"


def roof_z_p50(building: ReferenceBuilding) -> float | None:
    """Area-weighted median of the roof surfaces' mean vertex heights.

    A vertex median would be pulled toward whichever surfaces happen to have more
    vertices; weighting each surface by its area measures the roof a person sees.
    """
    surfaces = []
    for roof in building.roofs:
        ring = roof.points[:-1] if roof.points[0] == roof.points[-1] else roof.points
        area = roof.area()
        if ring and area > 0:
            surfaces.append((sum(p[2] for p in ring) / len(ring), area))
    if not surfaces:
        return None
    surfaces.sort()
    half = sum(a for _, a in surfaces) / 2.0
    running = 0.0
    for z, area in surfaces:
        running += area
        if running >= half:
            return z
    return surfaces[-1][0]  # pragma: no cover - float rounding guard


def building_row(building: ReferenceBuilding, tile: str, source_sha256: str) -> dict[str, Any]:
    """One output row for one reference building."""
    outline = roof_outline(building)
    has_roof = not outline.is_empty
    roof_z_max = max((p[2] for r in building.roofs for p in r.points), default=None)
    p50 = roof_z_p50(building)
    ground = (
        None
        if building.solid_z_min is None
        else building.solid_z_min + WALL_EXTRUSION_BELOW_GRADE_M
    )
    parcelle = (building.attributes.get("parcelle") or "").strip() or None
    return {
        "building_id": f"{tile}:{building.gml_id}",
        "tile": tile,
        "gml_id": building.gml_id,
        "is_grouped": building.is_grouped,
        "parcelle": parcelle,
        "outline": outline if has_roof else None,
        "footprint_area_m2": float(outline.area) if has_roof else None,
        "ground_z": ground,
        "ground_method": GROUND_METHOD if ground is not None else None,
        "roof_z_max": roof_z_max,
        "roof_z_p50": p50,
        "height_max_m": None if roof_z_max is None or ground is None else roof_z_max - ground,
        "height_p50_m": None if p50 is None or ground is None else p50 - ground,
        "roof_form": building.roof_type() if has_roof else None,
        "n_roof_surfaces": len(building.roofs),
        "model_vintage": MODEL_VINTAGE,
        "source_sha256": source_sha256,
        "mtl_roofs_version": __version__,
    }


def iter_rows(gmls: list[Path]) -> Iterator[dict[str, Any]]:
    """Rows for every building of every tile, tile by tile."""
    for gml in gmls:
        digest = sha256_file(gml)
        tile = tile_of(gml)
        for building in iter_buildings(gml):
            yield building_row(building, tile, digest)


def write_building_form(gmls: list[Path], dest: Path) -> int:
    """Write the area's rows to ``dest`` as GeoParquet; return the row count.

    Raises:
        ValueError: if two rows share a ``building_id``, which would mean the
            tile qualification no longer makes ids unique.
    """
    import geopandas as gpd

    rows = list(iter_rows(gmls))
    ids = [r["building_id"] for r in rows]
    if len(set(ids)) != len(ids):
        msg = f"{len(ids) - len(set(ids))} duplicate building_id(s) across {len(gmls)} tiles"
        raise ValueError(msg)
    frame = gpd.GeoDataFrame(rows, geometry="outline", crs=CRS_EPSG)
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    frame.to_parquet(partial, index=False)
    partial.replace(dest)
    return len(rows)
