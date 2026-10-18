"""Assignment, the evaluation grid, per-building scoring and pooling (#24)."""

from __future__ import annotations

import numpy as np
import pytest
import shapely

from mtl_roofs.evaluation.evaluate import (
    TEMPORAL_THRESHOLD_M,
    FootprintOutcome,
    evaluate,
    summarise,
)
from mtl_roofs.evaluation.grid import (
    CELL_M,
    building_cells,
    lidar_surface,
    predicted_surface,
    reference_surface,
)
from mtl_roofs.evaluation.matching import assign_buildings
from mtl_roofs.evaluation.report import BuildingResult
from mtl_roofs.evaluation.smoke import compare
from mtl_roofs.geometry.planes import Plane
from mtl_roofs.geometry.roof import RoofFace
from mtl_roofs.io.reference import ReferenceBuilding, RoofPolygon

X0, Y0 = 292_500.0, 5_035_500.0


def _box(x0: float, y0: float, x1: float, y1: float) -> shapely.Polygon:
    return shapely.box(X0 + x0, Y0 + y0, X0 + x1, Y0 + y1)


def _flat_roof(gml_id: str, poly: shapely.Polygon, z: float) -> ReferenceBuilding:
    ring = [(x, y, z) for x, y in poly.exterior.coords]
    return ReferenceBuilding(gml_id, [RoofPolygon(gml_id, ring)])


def _flat_face(poly: shapely.Polygon, z: float) -> RoofFace:
    return RoofFace(0, Plane((0.0, 0.0, 1.0), z), poly)


def _outcome(
    fid: str, poly: shapely.Polygon, z: float | None, lidar_z: float | None = None
) -> FootprintOutcome:
    """A footprint reconstructed as one flat face at ``z`` (``None``: it failed)."""
    xmin, ymin, xmax, ymax = poly.bounds
    gx, gy = np.meshgrid(np.arange(xmin + 0.1, xmax, 0.2), np.arange(ymin + 0.1, ymax, 0.2))
    height = lidar_z if lidar_z is not None else (z if z is not None else 0.0)
    points = np.column_stack([gx.ravel(), gy.ravel(), np.full(gx.size, height)])
    if z is None:
        return FootprintOutcome(fid, poly, "no-planes", roof_points=points, density=25.0)
    return FootprintOutcome(
        fid, poly, "ok", faces=[_flat_face(poly, z)], roof_points=points, density=25.0
    )


# --- assignment --------------------------------------------------------------------


def test_a_semi_detached_pair_goes_to_the_one_footprint_holding_both() -> None:
    footprints = {"fp-b": _box(0, 0, 12, 10), "fp-a": _box(20, 0, 30, 10)}
    outlines = {"east": _box(6, 0, 12, 10), "west": _box(0, 0, 6, 10)}
    result = assign_buildings(footprints, outlines)
    assert {k: v.footprint_id for k, v in result.items() if v} == {"east": "fp-b", "west": "fp-b"}
    assert all(v is not None and v.share == pytest.approx(1.0) for v in result.values())


def test_the_largest_share_wins_and_less_than_half_is_no_match() -> None:
    footprints = {"fp-a": _box(0, 0, 10, 10), "fp-b": _box(10, 0, 20, 10)}
    outlines = {"mostly-b": _box(7, 0, 17, 10), "spread": _box(-8, 20, 12, 30)}
    result = assign_buildings(footprints, outlines)
    assert result["mostly-b"] is not None
    assert result["mostly-b"].footprint_id == "fp-b"
    assert result["mostly-b"].share == pytest.approx(0.7)
    assert result["spread"] is None


def test_a_tie_goes_to_the_lower_footprint_id() -> None:
    footprints = {"fp-2": _box(5, 0, 15, 10), "fp-1": _box(-5, 0, 5, 10)}
    result = assign_buildings(footprints, {"straddling": _box(0, 0, 10, 10)})
    assert result["straddling"] is not None
    assert result["straddling"].footprint_id == "fp-1"


def test_an_empty_outline_is_never_assigned() -> None:
    result = assign_buildings({"fp-a": _box(0, 0, 10, 10)}, {"empty": shapely.Polygon()})
    assert result["empty"] is None


# --- the grid ----------------------------------------------------------------------


def test_cells_are_aligned_on_the_global_grid_and_eroded_by_one_cell() -> None:
    outline = _box(0.2, 0.3, 10.2, 6.3)
    x, y = building_cells(outline)
    assert np.allclose(np.mod(x - CELL_M / 2, CELL_M), 0.0)
    assert np.allclose(np.mod(y - CELL_M / 2, CELL_M), 0.0)
    eroded = outline.buffer(-CELL_M)
    assert shapely.contains_xy(eroded, x, y).all()
    # 9 x 5 m eroded: 18 x 10 cells, give or take a row where the grid is offset.
    assert 150 <= len(x) <= 180


def test_a_building_narrower_than_two_cells_has_no_cells() -> None:
    x, _ = building_cells(_box(0, 0, 0.9, 10))
    assert len(x) == 0


def test_the_reference_surface_is_the_highest_one() -> None:
    low = [(X0, Y0, 10.0), (X0 + 4, Y0, 10.0), (X0 + 4, Y0 + 4, 10.0), (X0, Y0 + 4, 10.0)]
    dormer = [(X0 + 1, Y0 + 1, 12.0), (X0 + 2, Y0 + 1, 12.0), (X0 + 2, Y0 + 2, 12.0)]
    wall = [(X0, Y0, 10.0), (X0 + 4, Y0, 10.0), (X0 + 4, Y0, 0.0), (X0, Y0, 0.0)]
    rings = [[*r, r[0]] for r in (low, dormer, wall)]
    x = np.array([X0 + 3.0, X0 + 1.8, X0 + 9.0])
    y = np.array([Y0 + 3.0, Y0 + 1.2, Y0 + 9.0])
    z = reference_surface(rings, x, y)
    assert z[0] == pytest.approx(10.0)
    assert z[1] == pytest.approx(12.0)
    assert np.isnan(z[2])


def test_the_reference_surface_follows_a_sloped_face() -> None:
    ring = [(X0, Y0, 10.0), (X0 + 4, Y0, 10.0), (X0 + 4, Y0 + 4, 12.0), (X0, Y0 + 4, 12.0)]
    z = reference_surface([[*ring, ring[0]]], np.array([X0 + 1.0]), np.array([Y0 + 1.0]))
    assert z[0] == pytest.approx(10.5)


def test_the_predicted_surface_is_undefined_off_the_faces() -> None:
    faces = [_flat_face(_box(0, 0, 4, 4), 15.0)]
    z = predicted_surface(faces, np.array([X0 + 1.0, X0 + 5.0]), np.array([Y0 + 1.0, Y0 + 1.0]))
    assert z[0] == pytest.approx(15.0)
    assert np.isnan(z[1])


def test_the_lidar_surface_is_the_median_per_cell() -> None:
    pts = np.array(
        [[X0 + 0.1, Y0 + 0.1, 1.0], [X0 + 0.2, Y0 + 0.2, 5.0], [X0 + 0.3, Y0 + 0.4, 2.0]]
    )
    z = lidar_surface(pts, np.array([X0 + 0.25, X0 + 1.25]), np.array([Y0 + 0.25, Y0 + 0.25]))
    assert z[0] == pytest.approx(2.0)
    assert np.isnan(z[1])


# --- scoring -----------------------------------------------------------------------


def test_a_perfect_reconstruction_scores_zero_with_full_coverage() -> None:
    poly = _box(0, 0, 10, 8)
    (row,) = evaluate([_outcome("fp-a", poly, 15.0)], [_flat_roof("b1", poly, 15.0)])
    assert row.status == "ok"
    assert row.rmse_m == pytest.approx(0.0)
    assert row.coverage == pytest.approx(1.0)


def test_a_constant_offset_is_the_rmse() -> None:
    poly = _box(0, 0, 10, 8)
    (row,) = evaluate([_outcome("fp-a", poly, 15.3, lidar_z=15.0)], [_flat_roof("b1", poly, 15.0)])
    assert row.rmse_m == pytest.approx(0.3)


def test_a_failed_footprint_costs_coverage_and_is_not_reconstructed() -> None:
    a, b = _box(0, 0, 10, 8), _box(20, 0, 30, 8)
    rows = evaluate(
        [_outcome("fp-a", a, 15.0), _outcome("fp-b", b, None, lidar_z=15.0)],
        [_flat_roof("ok", a, 15.0), _flat_roof("failed", b, 15.0)],
    )
    failed = next(r for r in rows if r.building_id == "failed")
    assert failed.status == "no-planes"
    assert failed.rmse_m is None
    assert failed.coverage == 0.0
    summary = summarise(rows)
    assert summary["coverage"] == pytest.approx(0.5)
    assert summary["vertical_rmse_m"] == pytest.approx(0.0)
    assert summary["buildings_reconstructed"] == 1
    assert summary["match_rate"] == pytest.approx(1.0)


def test_every_building_and_every_footprint_gets_a_row() -> None:
    a = _box(0, 0, 10, 8)
    buildings = [
        _flat_roof("matched", a, 15.0),
        _flat_roof("nowhere", _box(50, 50, 60, 60), 15.0),
        _flat_roof("Groupe123", a, 15.0),
    ]
    rows = evaluate(
        [_outcome("fp-a", a, 15.0), _outcome("fp-shed", _box(30, 0, 33, 3), 12.0)], buildings
    )
    status = {r.building_id: r.status for r in rows}
    assert status == {
        "matched": "ok",
        "nowhere": "no-match",
        "Groupe123": "grouped-reference",
        "fp-shed": "no-reference",
    }
    summary = summarise(rows)
    assert summary["match_rate"] == pytest.approx(0.5)
    assert summary["n_grouped"] == 1
    assert summary["n_no_reference"] == 1


def test_lidar_far_from_the_reference_is_suspect_temporal() -> None:
    poly = _box(0, 0, 10, 8)
    lidar = 15.0 + TEMPORAL_THRESHOLD_M + 1.0
    (row,) = evaluate(
        [_outcome("fp-a", poly, lidar, lidar_z=lidar)], [_flat_roof("b1", poly, 15.0)]
    )
    assert row.status == "suspect-temporal"
    assert row.temporal_offset_m == pytest.approx(TEMPORAL_THRESHOLD_M + 1.0)
    # Still scored, and still counted as reconstructed.
    assert row.rmse_m == pytest.approx(TEMPORAL_THRESHOLD_M + 1.0)
    assert summarise([row])["buildings_reconstructed"] == 1


def test_overall_rmse_pools_cells_not_buildings() -> None:
    def row(name: str, n: int, rmse: float) -> BuildingResult:
        return BuildingResult(
            name, "fp", "ok", n_ref_cells=n, n_valid_cells=n, sum_sq_m2=n * rmse**2, rmse_m=rmse
        )

    summary = summarise([row("big", 300, 0.1), row("small", 100, 0.5)])
    assert summary["vertical_rmse_m"] == pytest.approx(np.sqrt((300 * 0.01 + 100 * 0.25) / 400))
    assert summary["median_building_rmse_m"] == pytest.approx(0.3)


# --- the regression guard ----------------------------------------------------------

BASELINE = {
    "tolerances": {
        "vertical_rmse_m": 0.02,
        "coverage": -0.01,
        "buildings_reconstructed": 0,
        "orientation_error_median_deg": 0.5,
    },
    "metrics": {
        "overall": {
            "vertical_rmse_m": 0.40,
            "coverage": 0.95,
            "buildings_reconstructed": 30,
            "orientation_error_median_deg": None,
        }
    },
}


def test_changes_within_tolerance_or_for_the_better_pass() -> None:
    run = {"vertical_rmse_m": 0.415, "coverage": 0.945, "buildings_reconstructed": 31}
    assert compare(BASELINE, run) == []


@pytest.mark.parametrize(
    ("metric", "value"),
    [("vertical_rmse_m", 0.43), ("coverage", 0.93), ("buildings_reconstructed", 29)],
)
def test_a_change_beyond_tolerance_is_a_regression(metric: str, value: float) -> None:
    run = {"vertical_rmse_m": 0.40, "coverage": 0.95, "buildings_reconstructed": 30}
    run[metric] = value
    (message,) = compare(BASELINE, run)
    assert metric in message


def test_a_gated_metric_missing_from_the_run_is_a_regression() -> None:
    (message,) = compare(BASELINE, {"coverage": 0.95, "buildings_reconstructed": 30})
    assert "not produced" in message
