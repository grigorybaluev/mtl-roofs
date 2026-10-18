"""The breakdown report (#26)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from mtl_roofs.evaluation.breakdown import breakdown, write_breakdown
from mtl_roofs.evaluation.report import BuildingResult


def _row(
    name: str,
    roof_type: str = "flat",
    area: float = 150.0,
    density: float | None = 15.0,
    status: str = "ok",
    rmse: float = 0.2,
    lidar_derived: bool = False,
    warnings: tuple[str, ...] = (),
) -> BuildingResult:
    scored = status in ("ok", "suspect-temporal")
    return BuildingResult(
        building_id=name,
        footprint_id=None if status == "no-match" else f"fp-{name}",
        status=status,
        roof_type=roof_type,
        outline_area_m2=area,
        point_density=density,
        lidar_derived=None if status == "no-match" else lidar_derived,
        n_ref_cells=0 if status == "no-match" else 100,
        n_valid_cells=100 if scored else 0,
        sum_sq_m2=100 * rmse**2 if scored else 0.0,
        rmse_m=rmse if scored else None,
        coverage=(1.0 if scored else 0.0) if status != "no-match" else None,
        warnings=warnings,
    )


ROWS = [
    _row("a", "flat", 80.0, 22.0, rmse=0.1),
    _row("b", "pitched", 300.0, 12.0, rmse=0.5, warnings=("sliver-face",)),
    _row("c", "pitched", 300.0, 12.0, status="suspect-temporal", rmse=3.0),
    _row("d", "mixed", 2000.0, 4.0, status="no-planes"),
    _row("e", "flat", 120.0, None, status="no-match"),
    _row("f", "flat", 150.0, 18.0, rmse=0.3, lidar_derived=True),
]


def _get(records: list[dict], variant: str, axis: str, value: str) -> dict:
    (record,) = [
        r for r in records if r["variant"] == variant and r["axis"] == axis and r["bin"] == value
    ]
    return record


def test_every_axis_and_bin_is_present_even_when_empty() -> None:
    records = breakdown(ROWS)
    for variant in ("raw", "excluding suspect-temporal"):
        assert _get(records, variant, "size (m²)", "100-250")["n_buildings"] >= 1
        assert _get(records, variant, "point density (pts/m²)", "5-10")["n_buildings"] == 0
        assert _get(records, variant, "point density (pts/m²)", "5-10")["vertical_rmse_m"] is None


def test_excluding_suspect_temporal_removes_it_from_every_table() -> None:
    records = breakdown(ROWS)
    raw = _get(records, "raw", "roof type", "pitched")
    clean = _get(records, "excluding suspect-temporal", "roof type", "pitched")
    assert raw["n_buildings"] == 2
    assert clean["n_buildings"] == 1
    assert raw["vertical_rmse_m"] > clean["vertical_rmse_m"] == pytest.approx(0.5)


def test_a_failure_stays_in_its_bin_as_lost_coverage() -> None:
    record = _get(breakdown(ROWS), "raw", "roof type", "mixed")
    assert record["n_buildings"] == 1
    assert record["buildings_reconstructed"] == 0
    assert record["coverage"] == 0.0
    assert record["vertical_rmse_m"] is None


def test_lidar_derived_footprints_are_reported_separately() -> None:
    records = breakdown(ROWS)
    derived = _get(records, "raw", "overall", "LiDAR-derived footprints")
    independent = _get(records, "raw", "overall", "independent footprints")
    assert derived["n_buildings"] == 1
    assert derived["vertical_rmse_m"] == pytest.approx(0.3)
    assert independent["n_assigned"] == 4


def test_a_building_without_a_footprint_has_no_density_bin() -> None:
    record = _get(breakdown(ROWS), "raw", "point density (pts/m²)", "n/a")
    assert record["n_buildings"] == 1
    assert record["match_rate"] == 0.0


def test_the_report_files_carry_counts_statuses_and_warnings(tmp_path: Path) -> None:
    md, csv_path = write_breakdown(ROWS, tmp_path, "Evaluation of test")
    text = md.read_text()
    assert text.startswith("# Evaluation of test")
    assert "| `suspect-temporal` | 1 |" in text
    assert "`sliver-face` 1" in text
    assert "### By point density (pts/m²)" in text
    with csv_path.open() as handle:
        rows = list(csv.DictReader(handle))
    # 2 variants x (3 overall groups + 3 roof types + 4 sizes + 5 density bins).
    assert len(rows) == 2 * (3 + 3 + 4 + 5)
