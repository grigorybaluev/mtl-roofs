"""Footprint ids and the CARTO-BAT-TOIT reader (ADR 0005)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import box

from mtl_roofs.io.footprints import LIDAR_DERIVED_SOURCE, ROOF_LAYER, footprint_id, load_footprints

PHOTO_2016 = "photo aérienne 2016, (C) Communauté métropolitaine de Montréal"
LIDAR_2015 = f"{LIDAR_DERIVED_SOURCE}, Ville de Montréal"


def test_footprint_id_is_the_centroid_in_decimetres() -> None:
    assert footprint_id(292976.2, 5035764.6) == "fp-2929762-50357646"


def test_footprint_id_rounds_half_up() -> None:
    assert footprint_id(0.05, 0.149) == "fp-1-1"
    assert footprint_id(0.04999, 0.15) == "fp-0-2"


def _layer_zip(tmp_path: Path, rows: list[dict[str, object]]) -> Path:
    """Write rows as CARTO-BAT-TOIT.shp and zip it the way the city ships it."""
    shp_dir = tmp_path / "shp"
    shp_dir.mkdir()
    frame = gpd.GeoDataFrame(rows, geometry="geometry", crs=2950)
    frame.to_file(shp_dir / f"{ROOF_LAYER}.shp", engine="pyogrio")
    archive = tmp_path / "batiments.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for part in shp_dir.iterdir():
            zf.write(part, part.name)
    return archive


@pytest.fixture
def layer(tmp_path: Path) -> Path:
    pair = box(292970.0, 5035750.0, 292990.0, 5035770.0)
    return _layer_zip(
        tmp_path,
        [
            # The same polygon twice, as the city's layer does 10 times.
            {
                "methode": "photogrammétrie",
                "source": PHOTO_2016,
                "MAJ": "avril 2016",
                "geometry": pair,
            },
            {
                "methode": "modélisation automatique",
                "source": LIDAR_2015,
                "MAJ": "novembre 2015",
                "geometry": pair,
            },
            {
                "methode": "photogrammétrie",
                "source": PHOTO_2016,
                "MAJ": "avril 2016",
                "geometry": box(293500.0, 5035500.0, 293510.0, 5035510.0),
            },
        ],
    )


def test_duplicate_records_become_one_footprint(layer: Path) -> None:
    frame = load_footprints(layer)
    assert len(frame) == 2
    assert frame["footprint_id"].is_unique


def test_a_duplicate_with_a_lidar_record_is_flagged_lidar_derived(layer: Path) -> None:
    frame = load_footprints(layer).set_index("footprint_id")
    assert bool(frame.loc["fp-2929800-50357600", "lidar_derived"])
    assert not bool(frame.loc["fp-2935050-50355050", "lidar_derived"])


def test_crs_is_asserted_as_2950(layer: Path) -> None:
    assert load_footprints(layer).crs.to_epsg() == 2950


def test_bbox_limits_what_is_loaded(layer: Path) -> None:
    frame = load_footprints(layer, bbox=(293400.0, 5035400.0, 293600.0, 5035600.0))
    assert frame["footprint_id"].tolist() == ["fp-2935050-50355050"]
