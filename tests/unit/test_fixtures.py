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


def test_every_footprint_has_points_reference_and_outline(index: dict) -> None:
    """Fixtures are cut per footprint (ADR 0005)."""
    for footprint in index["footprints"]:
        fid = footprint["footprint_id"]
        assert (FIXTURES / "points" / f"{fid}.laz").exists(), fid
        assert (FIXTURES / "reference" / f"{fid}.json").exists(), fid
        assert (FIXTURES / "footprints" / f"{fid}.geojson").exists(), fid


def test_no_stray_fixture_files(index: dict) -> None:
    """A file left over from an older cut would be silently ignored by everything."""
    ids = {f["footprint_id"] for f in index["footprints"]}
    for sub in ("points", "reference", "footprints"):
        assert {p.stem for p in (FIXTURES / sub).iterdir()} == ids, sub


def test_every_reference_building_belongs_to_a_fixture_footprint(index: dict) -> None:
    footprints = {f["footprint_id"]: f for f in index["footprints"]}
    for building in index["buildings"]:
        footprint = footprints[building["footprint_id"]]
        assert building["building_id"] in footprint["reference_buildings"]
        assert building["share_in_footprint"] >= 0.5, building["building_id"]


def test_the_seed_buildings_are_all_present(index: dict) -> None:
    """The 20 buildings chosen in #15 stay in the set; neighbours are added around them."""
    assert sum(b["seed"] for b in index["buildings"]) == 20


def test_every_fixture_has_usable_point_support(index: dict) -> None:
    for footprint in index["footprints"]:
        fid = footprint["footprint_id"]
        assert footprint["n_points_in_footprint"] >= 200, fid
        assert footprint["point_density"] > 1.0, fid


def test_reference_polygons_are_closed_3d_rings() -> None:
    for path in (FIXTURES / "reference").glob("*.json"):
        data = json.loads(path.read_text())
        assert data["buildings"], path.name
        for building in data["buildings"]:
            assert building["roof_polygons"], path.name
            for ring in building["roof_polygons"]:
                assert len(ring) >= 4, path.name
                assert all(len(p) == 3 for p in ring), path.name
                assert ring[0] == ring[-1], f"{path.name}: ring is not closed"


def test_footprints_are_single_polygons_in_2950() -> None:
    for path in (FIXTURES / "footprints").glob("*.geojson"):
        data = json.loads(path.read_text())
        assert data["crs"]["properties"]["name"] == "urn:ogc:def:crs:EPSG::2950", path.name
        (feature,) = data["features"]
        assert feature["geometry"]["type"] == "Polygon", path.name
        assert feature["properties"]["footprint_id"] == path.stem


def test_baseline_declares_tolerances_for_every_headline_metric() -> None:
    baseline = json.loads((FIXTURES / "baseline_metrics.json").read_text())
    tolerances = baseline["tolerances"]
    for metric in ("vertical_rmse_m", "orientation_error_median_deg", "coverage", "match_rate"):
        assert metric in tolerances, metric


def test_baseline_fixture_count_matches_the_index(index: dict) -> None:
    """A drifted baseline is worse than none: it would silently pass a shrunken set."""
    baseline = json.loads((FIXTURES / "baseline_metrics.json").read_text())
    assert baseline["fixture_set"]["n_buildings"] == len(index["buildings"])
    assert baseline["fixture_set"]["n_footprints"] == len(index["footprints"])
