"""Breakdown bins must be fixed, so runs stay comparable."""

from __future__ import annotations

import pytest

from mtl_roofs.evaluation.report import BuildingResult, density_bin, size_bin


@pytest.mark.parametrize(
    ("density", "expected"),
    [(0.0, "<5"), (4.9, "<5"), (5.0, "5-10"), (18.09, "10-20"), (25.0, ">=20")],
)
def test_density_bins(density: float, expected: str) -> None:
    assert density_bin(density) == expected


@pytest.mark.parametrize(
    ("area", "expected"),
    [(50.0, "<100"), (111.0, "100-250"), (500.0, "250-1000"), (5000.0, ">=1000")],
)
def test_size_bins(area: float, expected: str) -> None:
    assert size_bin(area) == expected


def test_unmatched_building_still_produces_a_row() -> None:
    """Failures must appear in the results table, not vanish from it."""
    row = BuildingResult(
        building_id="1585788",
        footprint_id=None,
        status="no-match",
        roof_type="pitched",
        outline_area_m2=120.0,
    )
    assert row.rmse_m is None
    assert row.coverage is None
