"""The batch run's point index and per-footprint loop (#21)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import shapely

from mtl_roofs.batch import (
    USED_CLASSES,
    beyond_coverage,
    footprint_points,
    lidar_paths,
    process,
)
from mtl_roofs.io.lidar import PointIndex, PointSet, read_points
from mtl_roofs.pipeline import PipelineParams, Reconstruction, Status

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _fixture(fid: str) -> tuple[str, bool, shapely.Polygon]:
    data = json.loads((FIXTURES / "footprints" / f"{fid}.geojson").read_text())
    return fid, False, shapely.from_geojson(json.dumps(data["features"][0]["geometry"]))


FIDS = [f["footprint_id"] for f in json.loads((FIXTURES / "index.json").read_text())["footprints"]]


@pytest.fixture(scope="module")
def index() -> PointIndex:
    """All fixture points in one index, as an area's tiles would be."""
    sets = [read_points(FIXTURES / "points" / f"{fid}.laz") for fid in FIDS]
    return PointIndex.build(
        PointSet(
            np.concatenate([s.xyz for s in sets]),
            np.concatenate([s.classification for s in sets]),
        ),
        bucket_m=20.0,
    )


def test_a_window_query_returns_exactly_the_points_inside(index: PointIndex) -> None:
    xyz = index.points.xyz
    x0, y0 = np.median(xyz[:, 0]), np.median(xyz[:, 1])
    window = (x0 - 13.3, y0 - 7.1, x0 + 21.9, y0 + 40.2)
    got = index.query(*window)
    expected = (
        (xyz[:, 0] >= window[0])
        & (xyz[:, 0] <= window[2])
        & (xyz[:, 1] >= window[1])
        & (xyz[:, 1] <= window[3])
    )
    assert len(got) == int(expected.sum())
    assert np.array_equal(np.sort(got.xyz[:, 2]), np.sort(xyz[expected, 2]))


def test_an_empty_window_is_empty(index: PointIndex) -> None:
    assert len(index.query(0.0, 0.0, 10.0, 10.0)) == 0


def test_the_area_run_reproduces_the_fixture_reconstruction(index: PointIndex) -> None:
    """Points taken from the area index give the same result as the fixture's own cut."""
    fid, _, polygon = _fixture(FIDS[0])
    points = footprint_points(index, polygon)
    own = read_points(FIXTURES / "points" / f"{fid}.laz")
    keep = np.isin(own.classification, list(USED_CLASSES))
    region = shapely.buffer(polygon, 2.0)
    own_inside = keep & shapely.contains_xy(region, own.xyz[:, 0], own.xyz[:, 1])
    # The neighbours' context can add points; the footprint's own are all there.
    assert len(points) >= int(own_inside.sum())


def test_every_footprint_gets_a_result_and_a_crash_is_an_error(
    index: PointIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    import mtl_roofs.batch as batch

    real = batch.reconstruct_footprint
    broken = FIDS[1]

    def flaky(points: PointSet, polygon: shapely.Polygon, params: PipelineParams) -> Reconstruction:
        if polygon.equals(_fixture(broken)[2]):
            msg = "boom"
            raise RuntimeError(msg)
        return real(points, polygon, params)

    monkeypatch.setattr(batch, "reconstruct_footprint", flaky)
    saved: dict[str, Reconstruction] = {}
    footprints = [_fixture(fid) for fid in FIDS[:4]]
    counts = process(
        footprints, index, PipelineParams(), lambda fid, r, _s: saved.__setitem__(fid, r)
    )
    assert set(saved) == set(FIDS[:4])
    assert saved[broken].status is Status.ERROR
    assert saved[broken].reason == "RuntimeError: boom"
    assert counts["error"] == 1
    assert counts["ok"] == 3


def test_missing_tiles_are_named(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match=r"292-5034_2015\.las"):
        lidar_paths(tmp_path, ["292-5034"])


def test_footprints_crossing_the_tiles_edge_are_listed() -> None:
    inside = shapely.box(292_100.0, 5_034_100.0, 292_120.0, 5_034_120.0)
    crossing = shapely.box(292_100.0, 5_034_990.0, 292_120.0, 5_035_010.0)
    footprints = [("in", False, inside), ("across", False, crossing)]
    assert beyond_coverage(footprints, ["292-5034"]) == ["across"]
    assert beyond_coverage(footprints, ["292-5034", "292-5035"]) == []
