"""The fixture set is part of the evaluation contract, so it is asserted like code."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
MAX_BYTES = 2 * 1024 * 1024


@pytest.fixture(scope="module")
def index() -> dict:
    return json.loads((FIXTURES / "index.json").read_text())


def test_fixtures_are_real_montreal_data(index: dict) -> None:
    source = index["source"]
    assert source["crs"] == "EPSG:2950"
    assert source["vertical_datum"] == "CGVD28"
    assert source["lidar_tile"] == "292-5035"
    assert source["reference_tile"] == "CDNNDG03"


def test_every_roof_type_is_represented(index: dict) -> None:
    """A smoke set of only flat roofs would never exercise the topology solver."""
    kinds = {b["roof_type"] for b in index["buildings"]}
    assert {"flat", "mixed", "pitched", "complex"} <= kinds


def test_enough_buildings_to_be_meaningful(index: dict) -> None:
    assert len(index["buildings"]) >= 10


def test_no_fixture_exceeds_the_size_limit() -> None:
    oversized = [
        p.relative_to(FIXTURES).as_posix()
        for p in FIXTURES.rglob("*")
        if p.is_file() and p.stat().st_size > MAX_BYTES
    ]
    assert oversized == []


def test_points_and_reference_agree(index: dict) -> None:
    for building in index["buildings"]:
        bid = building["building_id"]
        assert (FIXTURES / "points" / f"{bid}.laz").exists(), bid
        assert (FIXTURES / "reference" / f"{bid}.json").exists(), bid


def test_every_fixture_has_usable_point_support(index: dict) -> None:
    for building in index["buildings"]:
        assert building["n_points"] >= 200, building["building_id"]
        assert building["point_density"] > 1.0, building["building_id"]


def test_reference_polygons_are_closed_3d_rings() -> None:
    for path in (FIXTURES / "reference").glob("*.json"):
        data = json.loads(path.read_text())
        assert data["roof_polygons"], path.name
        for ring in data["roof_polygons"]:
            assert len(ring) >= 4, path.name
            assert all(len(p) == 3 for p in ring), path.name
            assert ring[0] == ring[-1], f"{path.name}: ring is not closed"


def test_baseline_declares_tolerances_for_every_headline_metric() -> None:
    baseline = json.loads((FIXTURES / "baseline_metrics.json").read_text())
    tolerances = baseline["tolerances"]
    for metric in ("vertical_rmse_m", "orientation_error_median_deg", "coverage", "match_rate"):
        assert metric in tolerances, metric


def test_baseline_fixture_count_matches_the_index(index: dict) -> None:
    """A drifted baseline is worse than none: it would silently pass a shrunken set."""
    baseline = json.loads((FIXTURES / "baseline_metrics.json").read_text())
    assert baseline["fixture_set"]["n_buildings"] == len(index["buildings"])
