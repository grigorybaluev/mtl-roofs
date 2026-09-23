"""Per-footprint clipping and the roof filter (#17)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import shapely
from shapely.geometry import box

from mtl_roofs.geometry.clip import (
    CLASS_POLICY,
    ClassPolicy,
    ClipParams,
    ClipStatus,
    clip_footprint,
)
from mtl_roofs.io.lidar import CLASSIFICATION, PointSet, read_points

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
GROUND_Z = 40.0


def cloud(*groups: tuple[int, float, float, float, int]) -> PointSet:
    """Points in groups of ``(class, x, y, z, count)``, spread over a 1 m square."""
    rng = np.random.default_rng(7)
    xyz, cls = [], []
    for code, x, y, z, n in groups:
        xyz.append(np.column_stack([x + rng.random(n), y + rng.random(n), np.full(n, z)]))
        cls.append(np.full(n, code, dtype=np.uint8))
    return PointSet(np.vstack(xyz), np.concatenate(cls))


# A 10 m square footprint with ground all around it, 2 m outside.
FOOTPRINT = box(0.0, 0.0, 10.0, 10.0)
GROUND = [(2, gx, gy, GROUND_Z, 20) for gx in (-2.0, 11.0) for gy in (-2.0, 4.5, 11.0)]


def test_every_class_the_dataset_uses_has_an_explicit_policy() -> None:
    assert set(CLASSIFICATION) <= set(CLASS_POLICY)


def test_an_unknown_class_is_an_error_not_a_default() -> None:
    with pytest.raises(ValueError, match=r"\[29\]"):
        clip_footprint(cloud((29, 5.0, 5.0, 50.0, 10)), FOOTPRINT)


def test_building_points_inside_are_kept_and_outside_dropped() -> None:
    result = clip_footprint(cloud((6, 5.0, 5.0, 50.0, 80), (6, 20.0, 20.0, 50.0, 30)), FOOTPRINT)
    assert result.n_points == 80
    assert result.kept_by_class == {6: 80}


def test_ground_vegetation_and_noise_are_dropped_even_inside() -> None:
    inside = [(code, 5.0, 5.0, 50.0, 10) for code in (2, 3, 4, 5, 7)]
    result = clip_footprint(cloud(*inside, (6, 5.0, 5.0, 50.0, 60)), FOOTPRINT)
    assert result.kept_by_class == {6: 60}


@pytest.mark.parametrize("code", [1, 28])
def test_conditional_classes_are_kept_only_above_the_height_threshold(code: int) -> None:
    assert CLASS_POLICY[code] is ClassPolicy.KEEP_IF_ELEVATED
    low = (code, 2.0, 2.0, GROUND_Z + 1.0, 30)  # a car, a fence
    lower_roof = (code, 6.0, 6.0, GROUND_Z + 1.8, 40)  # the lowest roofs are ~1.6 m up
    result = clip_footprint(cloud(*GROUND, low, lower_roof, (6, 5.0, 5.0, 50.0, 60)), FOOTPRINT)
    assert result.kept_by_class == {6: 60, code: 40}


def test_without_ground_the_conditional_classes_are_dropped() -> None:
    result = clip_footprint(cloud((28, 5.0, 5.0, 50.0, 30), (6, 5.0, 5.0, 50.0, 60)), FOOTPRINT)
    assert not result.ground_available
    assert result.kept_by_class == {6: 60}


def test_too_few_points_is_a_status_not_a_crash() -> None:
    result = clip_footprint(cloud((6, 5.0, 5.0, 50.0, 10)), FOOTPRINT)
    assert result.status is ClipStatus.TOO_FEW_POINTS
    empty = clip_footprint(cloud((6, 50.0, 50.0, 50.0, 10)), FOOTPRINT)
    assert empty.status is ClipStatus.TOO_FEW_POINTS
    assert empty.n_points == 0


def test_the_buffer_is_configurable() -> None:
    points = cloud((6, 5.0, 5.0, 50.0, 60), (6, 10.1, 5.0, 50.0, 20))  # 0.1-1.1 m outside
    assert clip_footprint(points, FOOTPRINT).n_points == 60
    assert clip_footprint(points, FOOTPRINT, ClipParams(buffer_m=1.5)).n_points == 80


def test_density_is_retained_points_per_square_metre_of_footprint() -> None:
    result = clip_footprint(cloud((6, 5.0, 5.0, 50.0, 200)), FOOTPRINT)
    assert result.density == pytest.approx(2.0)


# ---------------------------------------------------------------- real fixtures
#: Points retained per fixture footprint with the default parameters. Clipping is
#: deterministic, so any change here is a behaviour change to justify in the PR.
RETAINED = {
    "fp-2924989-50356656": 4663,
    "fp-2926302-50356572": 14297,
    "fp-2927091-50356466": 3049,
    "fp-2927143-50353603": 1983,
    "fp-2927284-50359379": 1472,
    "fp-2927512-50354852": 1966,
    "fp-2927598-50357091": 2665,
    "fp-2927692-50357502": 1254,
    "fp-2927777-50357945": 4461,
    "fp-2928011-50357337": 2913,
    "fp-2928474-50358723": 4336,
    "fp-2928641-50356522": 2095,
    "fp-2928770-50354596": 889,
    "fp-2928850-50356864": 4712,
    "fp-2929000-50353029": 4460,
    "fp-2929191-50357170": 5202,
    "fp-2929243-50354806": 2392,
    "fp-2929353-50356578": 4550,
    "fp-2929526-50357388": 1476,
    "fp-2929762-50357646": 3234,
}


def _footprint(fid: str) -> shapely.Polygon:
    data = json.loads((FIXTURES / "footprints" / f"{fid}.geojson").read_text())
    return shapely.from_geojson(json.dumps(data["features"][0]["geometry"]))


def _reference_z(rings: list[list[list[float]]], x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Height of the highest reference roof face above each point; NaN where none is."""
    out = np.full(len(x), np.nan)
    for ring in rings:
        pts = np.asarray(ring)[:-1]
        normal = np.cross(pts[1] - pts[0], pts[2] - pts[0])
        polygon = shapely.Polygon(pts[:, :2])
        if abs(normal[2]) < 1e-9 or not polygon.is_valid:
            continue
        z = pts[0, 2] - (normal[0] * (x - pts[0, 0]) + normal[1] * (y - pts[0, 1])) / normal[2]
        inside = shapely.contains_xy(polygon, x, y)
        out = np.where(inside & (np.isnan(out) | (z > out)), z, out)
    return out


@pytest.fixture(scope="module")
def clipped() -> dict[str, tuple[object, list[list[list[float]]]]]:
    index = json.loads((FIXTURES / "index.json").read_text())
    results = {}
    for footprint in index["footprints"]:
        fid = footprint["footprint_id"]
        result = clip_footprint(read_points(FIXTURES / "points" / f"{fid}.laz"), _footprint(fid))
        reference = json.loads((FIXTURES / "reference" / f"{fid}.json").read_text())
        rings = [r for b in reference["buildings"] for r in b["roof_polygons"]]
        results[fid] = (result, rings)
    return results


def test_every_fixture_clips_to_a_usable_roof(clipped: dict) -> None:
    for fid, (result, _) in clipped.items():
        assert result.status is ClipStatus.OK, fid
        assert result.ground_available, fid


def test_retained_counts_are_stable(clipped: dict) -> None:
    assert {fid: result.n_points for fid, (result, _) in clipped.items()} == RETAINED


def test_retained_points_are_mostly_on_the_reference_roofs(clipped: dict) -> None:
    """Across the set, not per fixture: two fixtures legitimately disagree with the
    reference (a systematic +0.56 m offset on fp-2928641, faces under an overhang on
    fp-2927284). That is for the evaluation to report, not for the filter to hide."""
    near = total = 0
    for result, rings in clipped.values():
        x, y, z = result.roof.xyz.T
        dz = np.abs(z - _reference_z(rings, x, y))
        near += int((dz < 0.5).sum())
        total += len(dz)
    assert near / total > 0.85
