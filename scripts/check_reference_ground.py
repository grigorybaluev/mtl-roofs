"""Check the reference model's ground level against LiDAR ground points (#91).

Hypothesis, from the publisher's method note: walls are extruded to 3 m below grade,
so ground = lowest solid vertex + 3 m (``ground_method = lod2_minus3`` in the
building-form export). This compares that, per building, with the 2015 LiDAR's
ground-class (2) returns in a ring 1-4 m outside the building's outline: the median of
those returns, and their 5th percentile (the downhill side; see
``pipeline.ground_height``).

Decision rule, fixed before running: if the median |difference| is >= 0.5 m, the
export's ground must come from LiDAR rather than from the model.

Needs `data fetch -a verdun --only reference` + `export building-form -a verdun`, and
`data fetch -a verdun-ground-sample --only lidar`.

    pixi run python scripts/check_reference_ground.py
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely

from mtl_roofs.config import StudyArea
from mtl_roofs.io.lidar import PointIndex, tile_member

ROOT = Path(__file__).resolve().parents[1]
FORM = ROOT / "data" / "processed" / "verdun" / "building_form.parquet"
SAMPLE = StudyArea.load("verdun-ground-sample")
RAW = ROOT / "data" / "raw" / SAMPLE.name
RING_INNER_M, RING_OUTER_M = 1.0, 4.0
MIN_GROUND_POINTS = 10
THRESHOLD_M = 0.5


def main() -> None:
    form = gpd.read_parquet(FORM)
    bbox = SAMPLE.bbox
    extent = shapely.box(bbox.xmin, bbox.ymin, bbox.xmax, bbox.ymax)
    form = form[form.outline.notna()]
    inside = form[form.outline.buffer(RING_OUTER_M).within(extent)]

    paths = [RAW / tile_member(t) for t in bbox.lidar_tiles()]
    index = PointIndex.from_las(
        paths, (bbox.xmin, bbox.ymin, bbox.xmax, bbox.ymax), keep_classes=frozenset({2})
    )

    rows = []
    for row in inside.itertuples():
        ring = row.outline.buffer(RING_OUTER_M).difference(row.outline.buffer(RING_INNER_M))
        x0, y0, x1, y1 = ring.bounds
        pts = index.query(x0, y0, x1, y1).xyz
        if len(pts) == 0:
            continue
        pts = pts[shapely.contains_xy(ring, pts[:, 0], pts[:, 1])]
        if len(pts) < MIN_GROUND_POINTS:
            continue
        z = pts[:, 2]
        rows.append(
            (row.building_id, row.roof_form, row.ground_z, float(np.median(z)),
             float(np.percentile(z, 5)), len(z))
        )

    ids, forms, model, med, p5, n = zip(*rows, strict=True)
    d_med = np.asarray(model) - np.asarray(med)
    d_p5 = np.asarray(model) - np.asarray(p5)

    def stats(d: np.ndarray) -> dict[str, float]:
        a = np.abs(d)
        return {
            "signed_median_m": round(float(np.median(d)), 3),
            "abs_median_m": round(float(np.median(a)), 3),
            "abs_p90_m": round(float(np.percentile(a, 90)), 3),
            "share_abs_ge_0.5m": round(float((a >= THRESHOLD_M).mean()), 4),
        }

    worst = np.argsort(-np.abs(d_med))[:10]
    report = {
        "buildings_in_sample": int(len(inside)),
        "buildings_compared": len(rows),
        "skipped_too_few_ground_points": int(len(inside) - len(rows)),
        "ground_points_per_building_median": int(np.median(n)),
        "model_minus_lidar_median": stats(d_med),
        "model_minus_lidar_p5": stats(d_p5),
        "decision": (
            "keep lod2_minus3" if np.median(np.abs(d_med)) < THRESHOLD_M else "use lidar ground"
        ),
        "worst_10": [
            {"building_id": ids[i], "roof_form": forms[i], "model_minus_lidar_m": round(float(d_med[i]), 2)}
            for i in worst
        ],
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
