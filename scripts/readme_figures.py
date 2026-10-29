"""Render the README's example buildings: one typical building per roof type (#34).

"Typical" is chosen by rule, not by eye: among scored buildings that are the only
reference building on their footprint, the one whose RMSE is closest to the median
RMSE of its roof type. The figures use the failure gallery's four panels, so the
README shows the same picture of a median case that the gallery shows of the worst.

Needs `mtl-roofs evaluate -a cdn-ndg-03` first. Writes docs/img/typical-<roof type>.png.

    pixi run python scripts/readme_figures.py
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from statistics import median

from mtl_roofs.cli import _with_roof_points
from mtl_roofs.config import Settings, StudyArea
from mtl_roofs.evaluation.gallery import _render, difference
from mtl_roofs.io.postgis import area_buildings, area_outcomes, connect, load_building_results
from mtl_roofs.io.reference import roof_outline

AREA = "cdn-ndg-03"
ROOF_TYPES = ("pitched", "mixed", "flat")
OUT = Path(__file__).resolve().parents[1] / "docs" / "img"


def main() -> None:
    """Pick one typical building per roof type and render it."""
    settings = Settings()
    with connect(settings) as conn:
        rows = load_building_results(conn, AREA)
        outcomes = area_outcomes(conn, AREA)
        buildings = {b.gml_id: b for b in area_buildings(conn, AREA)}

    per_footprint = Counter(r.footprint_id for r in rows if r.footprint_id is not None)
    picks = []
    for roof_type in ROOF_TYPES:
        scored = [r for r in rows if r.roof_type == roof_type and r.rmse_m is not None]
        mid = median(r.rmse_m for r in scored if r.rmse_m is not None)
        alone = [r for r in scored if r.status == "ok" and per_footprint[r.footprint_id] == 1]
        pick = min(alone, key=lambda r: (abs((r.rmse_m or 0.0) - mid), r.building_id))
        print(f"{roof_type}: median {mid:.2f} m, picked {pick.building_id} ({pick.rmse_m:.2f} m)")
        picks.append((roof_type, pick))

    wanted = {p.footprint_id for _, p in picks}
    footprints = {
        o.footprint_id: o
        for o in _with_roof_points(
            [o for o in outcomes if o.footprint_id in wanted], StudyArea.load(AREA), settings
        )
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for roof_type, row in picks:
        assert row.footprint_id is not None
        building = buildings[row.building_id]
        outline = roof_outline(building)
        footprint = footprints[row.footprint_id]
        path = OUT / f"typical-{roof_type}.png"
        _render(path, row, footprint, building, outline, difference(building, footprint, outline))
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
