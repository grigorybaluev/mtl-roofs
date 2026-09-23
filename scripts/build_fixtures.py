"""Regenerate tests/fixtures/ from the local cdn-ndg-03 data (ADR 0005).

Fixtures are cut **per footprint**: each of the 20 seed reference buildings is taken
to the CARTO-BAT-TOIT polygon that contains it, and the fixture holds that whole
footprint, its LiDAR plus 2 m of context, and every reference building assigned to it.
A fixture cut to one reference building would give the reconstruction half a roof.

Needs `mtl-roofs data fetch -a cdn-ndg-03` first. Regenerating the fixtures is its own
pull request (see tests/fixtures/README.md).

    pixi run python scripts/build_fixtures.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import laspy
import numpy as np
import shapely
from shapely.ops import unary_union

from mtl_roofs.io.footprints import load_footprints
from mtl_roofs.io.lidar import tile_member
from mtl_roofs.io.reference import ReferenceBuilding, iter_buildings

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "cdn-ndg-03"
OUT = ROOT / "tests" / "fixtures"
FOOTPRINTS_ZIP = RAW / "batiments_2d_2016_arrondissements.zip"
REFERENCE_GML = RAW / "CDNNDG03_2016.gml"

#: The 20 reference buildings chosen in #15, stratified by roof type. Kept as seeds
#: so the set still exercises every code path.
SEEDS = [
    "3322085", "3322002", "3322284", "3321697", "3321594", "3322143",  # pitched
    "3322132", "3321560", "3321999", "3321611", "3321923",  # mixed
    "3322256", "1291864", "3321806", "3322013", "3321857",  # flat
    "3321588", "3321636", "3322101", "3321887",  # complex
]  # fmt: skip
#: Metres of LiDAR kept around each footprint, for the clipping buffer to work with.
CONTEXT_M = 2.0
#: A reference building belongs to the footprint holding at least this share of it.
MIN_SHARE = 0.5
#: Fixture label for buildings with this many reference roof faces or more.
COMPLEX_FACES = 40
MAX_BYTES = 2 * 1024 * 1024


def outline(building: ReferenceBuilding) -> shapely.Geometry:
    """2D union of a reference building's roof surfaces: its roof outline."""
    parts = [shapely.Polygon([p[:2] for p in r.points]).buffer(0) for r in building.roofs]
    return unary_union([p for p in parts if p.area > 0])


def label(building: ReferenceBuilding) -> str:
    """Fixture stratum: the evaluation's roof type, or ``complex`` for many faces."""
    return "complex" if len(building.roofs) >= COMPLEX_FACES else building.roof_type()


def main() -> None:
    """Rebuild every fixture file, the index and the baseline's inventory."""
    buildings = {b.gml_id: b for b in iter_buildings(REFERENCE_GML) if b.roofs}
    outlines = {gid: outline(b) for gid, b in buildings.items()}
    bounds = unary_union(list(outlines.values())).bounds
    footprints = load_footprints(FOOTPRINTS_ZIP, bbox=bounds).set_index("footprint_id")

    # ADR 0005: each reference building goes to the footprint containing the largest
    # share of its outline, if >= 0.5; ties go to the lower footprint id. The pipeline's
    # own implementation belongs in evaluation/matching.py (#24).
    tree = shapely.STRtree(footprints.geometry.values)
    fp_ids = footprints.index.to_numpy()
    assigned: dict[str, tuple[str, float]] = {}
    for gid, shape in outlines.items():
        best: tuple[float, str] | None = None
        for i in tree.query(shape):
            share = shape.intersection(footprints.geometry.iloc[i]).area / shape.area
            key = (-share, str(fp_ids[i]))
            if share >= MIN_SHARE and (best is None or key < (-best[0], best[1])):
                best = (share, str(fp_ids[i]))
        if best is not None:
            assigned[gid] = (best[1], best[0])

    chosen = sorted({assigned[s][0] for s in SEEDS})
    members = {fp: sorted(g for g, (f, _) in assigned.items() if f == fp) for fp in chosen}
    clips = {fp: footprints.loc[fp, "geometry"].buffer(CONTEXT_M) for fp in chosen}

    for sub in ("points", "reference", "footprints"):
        for old in (OUT / sub).glob("*"):
            old.unlink()
        (OUT / sub).mkdir(exist_ok=True)

    points = cut_points(clips)
    index_fps, index_buildings = [], []
    for fp in chosen:
        geom = footprints.loc[fp, "geometry"]
        las = points[fp]
        laz = OUT / "points" / f"{fp}.laz"
        las.write(laz)
        x, y = np.asarray(las.x), np.asarray(las.y)
        inside = shapely.contains_xy(geom, x, y)
        cls = np.asarray(las.classification)
        index_fps.append(
            {
                "footprint_id": fp,
                "lidar_derived": bool(footprints.loc[fp, "lidar_derived"]),
                "area_m2": round(geom.area, 1),
                "bbox": [round(v, 2) for v in geom.bounds],
                "reference_buildings": members[fp],
                "seeds": [s for s in SEEDS if assigned[s][0] == fp],
                "n_points": len(las.points),
                "n_points_in_footprint": int(inside.sum()),
                "n_points_class6_in_footprint": int((inside & (cls == 6)).sum()),
                "point_density": round(int(inside.sum()) / geom.area, 2),
                "laz_bytes": laz.stat().st_size,
            }
        )
        write_footprint(fp, geom, footprints.loc[fp])
        reference = []
        for gid in members[fp]:
            b = buildings[gid]
            reference.append(
                {
                    "gml_id": gid,
                    "share_in_footprint": round(assigned[gid][1], 3),
                    "roof_polygons": [
                        [[round(c, 3) for c in p] for p in r.points] for r in b.roofs
                    ],
                }
            )
            index_buildings.append(
                {
                    "building_id": gid,
                    "footprint_id": fp,
                    "seed": gid in SEEDS,
                    "share_in_footprint": round(assigned[gid][1], 3),
                    "roof_type": label(b),
                    "reference_faces": len(b.roofs),
                    "reference_roof_area_m2": round(b.roof_area(), 1),
                    "reference_mean_slope_deg": round(b.mean_slope(), 2),
                    "reference_steep_fraction": round(b.steep_fraction(), 3),
                    "outline_area_m2": round(outlines[gid].area, 1),
                }
            )
        (OUT / "reference" / f"{fp}.json").write_text(
            json.dumps({"footprint_id": fp, "buildings": reference}, indent=1) + "\n"
        )

    index = {
        "source": {
            "lidar_tile": "292-5035",
            "reference_tile": "CDNNDG03",
            "footprint_layer": "CARTO-BAT-TOIT",
            "study_area": "cdn-ndg-03",
            "crs": "EPSG:2950",
            "vertical_datum": "CGVD28",
            "context_m": CONTEXT_M,
        },
        "footprints": index_fps,
        "buildings": index_buildings,
    }
    (OUT / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n")

    # Only the inventory: metric values and tolerances are never touched here.
    baseline_path = OUT / "baseline_metrics.json"
    baseline = json.loads(baseline_path.read_text())
    baseline["fixture_set"] = {
        "n_footprints": len(index_fps),
        "n_buildings": len(index_buildings),
        "lidar_tile": "292-5035",
        "reference_tile": "CDNNDG03",
        "footprint_layer": "CARTO-BAT-TOIT",
        "crs": "EPSG:2950",
    }
    types = Counter(b["roof_type"] for b in index_buildings)
    for kind, block in baseline["metrics"]["by_roof_type"].items():
        block["n"] = types[kind]
    baseline_path.write_text(json.dumps(baseline, indent=2, ensure_ascii=False) + "\n")

    sizes = [p.stat().st_size for p in OUT.rglob("*") if p.is_file()]
    assert max(sizes) < MAX_BYTES, "a fixture file exceeds 2 MB"
    print(f"{len(chosen)} footprints, {len(index_buildings)} reference buildings {dict(types)}")
    print(f"total {sum(sizes) / 1e6:.2f} MB, largest {max(sizes) / 1e3:.0f} kB")


def cut_points(clips: dict[str, shapely.Geometry]) -> dict[str, laspy.LasData]:
    """LiDAR inside each clip polygon, read from whichever local tiles it touches."""
    tiles = sorted(
        {
            tile_member(f"{int(x // 1000)}-{int(y // 1000)}").removesuffix("_2015.las")
            for clip in clips.values()
            for x, y in shapely.get_coordinates(shapely.envelope(clip))
        }
    )
    chunks: dict[str, list[laspy.ScaleAwarePointRecord]] = {fp: [] for fp in clips}
    header: laspy.LasHeader | None = None
    for tile in tiles:
        with laspy.open(RAW / tile_member(tile)) as reader:
            header = header or reader.header
            for chunk in reader.chunk_iterator(2_000_000):
                x, y = np.asarray(chunk.x), np.asarray(chunk.y)
                for fp, clip in clips.items():
                    xmin, ymin, xmax, ymax = clip.bounds
                    near = (x >= xmin) & (x <= xmax) & (y >= ymin) & (y <= ymax)
                    if not near.any():
                        continue
                    idx = np.flatnonzero(near)
                    keep = idx[shapely.contains_xy(clip, x[idx], y[idx])]
                    if len(keep):
                        chunks[fp].append(chunk[keep])
    assert header is not None
    out = {}
    for fp, parts in chunks.items():
        fresh = laspy.LasHeader(point_format=header.point_format, version=header.version)
        las = laspy.LasData(fresh)
        las.header.scales, las.header.offsets = header.scales, header.offsets
        las.header.vlrs.extend(header.vlrs)
        las.points = laspy.ScaleAwarePointRecord(
            np.concatenate([p.array for p in parts]), header.point_format, header.scales,
            header.offsets,
        )  # fmt: skip
        out[fp] = las
    return out


def write_footprint(fp: str, geom: shapely.Geometry, row: object) -> None:
    """Write the footprint polygon and its provenance as a one-feature GeoJSON."""
    feature = {
        "type": "Feature",
        "properties": {
            "footprint_id": fp,
            "lidar_derived": bool(row["lidar_derived"]),  # type: ignore[index]
            "methode": row["methode"],  # type: ignore[index]
            "source": row["source"],  # type: ignore[index]
            "MAJ": row["MAJ"],  # type: ignore[index]
        },
        "geometry": json.loads(shapely.to_geojson(shapely.set_precision(geom, 0.001))),
    }
    collection = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::2950"}},
        "features": [feature],
    }
    (OUT / "footprints" / f"{fp}.geojson").write_text(
        json.dumps(collection, indent=1, ensure_ascii=False) + "\n"
    )


if __name__ == "__main__":
    main()
