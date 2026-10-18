"""The failure gallery and its diagnoses (#27)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import shapely

from mtl_roofs.evaluation.evaluate import FootprintOutcome, evaluate
from mtl_roofs.evaluation.gallery import Difference, diagnose, write_gallery
from mtl_roofs.evaluation.report import BuildingResult
from mtl_roofs.geometry.planes import Plane
from mtl_roofs.geometry.roof import RoofFace
from mtl_roofs.io.reference import ReferenceBuilding, RoofPolygon

X0, Y0 = 292_500.0, 5_035_500.0


def _row(**overrides: object) -> BuildingResult:
    base: dict[str, object] = {
        "building_id": "b",
        "footprint_id": "fp",
        "status": "ok",
        "rmse_m": 1.0,
        "coverage": 1.0,
        "share": 1.0,
        "orientation_median_deg": 1.0,
    }
    base.update(overrides)
    return BuildingResult(**base)  # type: ignore[arg-type]


def _diff(dz: list[float]) -> Difference:
    values = np.asarray(dz, dtype=np.float64)
    return Difference(np.zeros(len(values)), np.zeros(len(values)), values)


def test_a_temporal_suspect_is_diagnosed_as_change() -> None:
    row = _row(status="suspect-temporal", temporal_offset_m=3.2)
    assert "+3.2 m" in diagnose(row)
    assert "changed" in diagnose(row)


def test_a_failure_carries_its_reason() -> None:
    row = _row(status="not-watertight", rmse_m=None, reason="an edge is not shared")
    assert diagnose(row) == "Not reconstructed (`not-watertight`): an edge is not shared."


def test_missing_coverage_comes_before_the_height_pattern() -> None:
    assert "covers only 60%" in diagnose(_row(coverage=0.6), _diff([0.0, 3.0]))


def test_a_uniform_offset_is_the_whole_roof() -> None:
    row = _row(rmse_m=1.2)
    assert diagnose(row, _diff([-1.2] * 50)) == "The whole roof sits 1.2 m below the reference."


def test_tilted_planes() -> None:
    assert "6.5°" in diagnose(_row(orientation_median_deg=6.5), _diff([0.1, -0.1]))


def test_one_part_off_versus_isolated_spikes() -> None:
    part = diagnose(_row(), _diff([2.0] * 20 + [0.1] * 80))
    assert part.startswith("One part of the roof is off: 20%")
    assert "mostly too high" in part
    spikes = diagnose(_row(), _diff([-3.0] * 2 + [0.1] * 98))
    assert spikes.startswith("Small, isolated errors: 2%")
    assert "mostly too low" in spikes


def test_the_gallery_renders_the_worst_and_lists_the_unscored(tmp_path: Path) -> None:
    def flat(gml_id: str, poly: shapely.Polygon, z: float) -> ReferenceBuilding:
        ring = [(x, y, z) for x, y in poly.exterior.coords]
        return ReferenceBuilding(gml_id, [RoofPolygon(gml_id, ring)])

    good, bad, failed = (shapely.box(X0 + i * 20, Y0, X0 + i * 20 + 10, Y0 + 8) for i in range(3))
    points = np.array([[X0 + 1, Y0 + 1, 15.0]])
    outcomes = [
        FootprintOutcome(
            f"fp-{name}",
            poly,
            "ok",
            faces=[RoofFace(0, Plane((0.0, 0.0, 1.0), z), poly)],
            roof_points=points,
        )
        for name, poly, z in (("good", good, 15.0), ("bad", bad, 17.0))
    ]
    outcomes.append(FootprintOutcome("fp-failed", failed, "no-planes", roof_points=points))
    buildings = [flat("good", good, 15.0), flat("bad", bad, 15.0), flat("failed", failed, 15.0)]
    rows = evaluate(outcomes, buildings)

    page = write_gallery(
        rows, {o.footprint_id: o for o in outcomes}, {b.gml_id: b for b in buildings}, tmp_path, n=1
    )
    text = page.read_text()
    assert "### 1. `bad` on `fp-bad`" in text
    assert "`good`" not in text.split("## Buildings not scored")[0]
    assert "The whole roof sits 2.0 m above the reference." in text
    assert "| `failed` | `fp-failed` | `no-planes` |" in text
    assert (tmp_path / "01-bad.png").stat().st_size > 0
