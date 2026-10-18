"""Plane orientation error, plane matching and face-count agreement (#25)."""

from __future__ import annotations

import numpy as np
import pytest
import shapely

from mtl_roofs.evaluation.evaluate import FootprintOutcome, evaluate, summarise
from mtl_roofs.evaluation.structure import score_structure
from mtl_roofs.geometry.planes import Plane
from mtl_roofs.geometry.roof import RoofFace
from mtl_roofs.io.reference import ReferenceBuilding, RoofPolygon

X0, Y0 = 292_500.0, 5_035_500.0
K = np.tan(np.radians(35.0))


def _plane(z0: float, gx: float, gy: float) -> Plane:
    """``z = z0 + gx (x - X0) + gy (y - Y0)``, normal up."""
    n = np.array([-gx, -gy, 1.0]) / np.sqrt(gx**2 + gy**2 + 1.0)
    return Plane((float(n[0]), float(n[1]), float(n[2])), float(n @ [X0, Y0, z0]))


def _ring(poly: shapely.Polygon, plane: Plane) -> list[tuple[float, float, float]]:
    nx, ny, nz = plane.normal
    return [(x, y, (plane.offset - nx * x - ny * y) / nz) for x, y in poly.exterior.coords]


def _box(x0: float, y0: float, x1: float, y1: float) -> shapely.Polygon:
    return shapely.box(X0 + x0, Y0 + y0, X0 + x1, Y0 + y1)


# A 10 x 8 m gable: south face rising to the ridge at y = 4, north face falling.
SOUTH, NORTH = _box(0, 0, 10, 4), _box(0, 4, 10, 8)
P_SOUTH, P_NORTH = _plane(17.2, 0.0, K), _plane(20.0 + 4 * K, 0.0, -K)
OUTLINE = _box(0, 0, 10, 8)
REFERENCE = [_ring(SOUTH, P_SOUTH), _ring(NORTH, P_NORTH)]


def test_an_exact_gable_matches_both_planes_with_no_error() -> None:
    faces = [RoofFace(0, P_SOUTH, SOUTH), RoofFace(1, P_NORTH, NORTH)]
    score = score_structure(faces, REFERENCE, OUTLINE)
    assert score.angles_deg == pytest.approx((0.0, 0.0), abs=1e-6)
    assert (score.n_unmatched_pred, score.n_unmatched_ref) == (0, 0)
    assert (score.n_pred_faces, score.n_ref_faces) == (2, 2)
    assert score.face_count_agreement == 1.0


def test_the_error_is_the_angle_between_normals() -> None:
    tilted = _plane(17.2, np.tan(np.radians(5.0)), K)
    faces = [RoofFace(0, tilted, SOUTH), RoofFace(1, P_NORTH, NORTH)]
    score = score_structure(faces, REFERENCE, OUTLINE)
    expected = np.degrees(np.arccos(np.dot(tilted.normal, P_SOUTH.normal)))
    assert sorted(score.angles_deg) == pytest.approx([0.0, expected], abs=1e-6)


def test_planes_overlapping_too_little_are_not_matched() -> None:
    # One flat face over the whole roof: IoU 0.5 with each half, so it matches one of
    # them; the other reference plane is left unmatched.
    flat = _plane(18.0, 0.0, 0.0)
    score = score_structure([RoofFace(0, flat, OUTLINE)], REFERENCE, OUTLINE)
    assert len(score.angles_deg) == 1
    assert score.n_unmatched_ref == 1
    # A face covering a quarter of one reference face (IoU 0.25) matches nothing.
    small = score_structure([RoofFace(0, P_SOUTH, _box(0, 0, 2.5, 4))], REFERENCE, OUTLINE)
    assert small.angles_deg == ()
    assert (small.n_unmatched_pred, small.n_unmatched_ref) == (1, 2)


def test_matching_is_greedy_by_smallest_angle() -> None:
    # Two predicted planes both overlap the south reference face; the better one wins.
    near = _plane(17.2, 0.0, np.tan(np.radians(36.0)))
    far = _plane(17.2, 0.0, np.tan(np.radians(30.0)))
    faces = [RoofFace(0, far, _box(0, 0, 10, 2)), RoofFace(1, near, _box(0, 2, 10, 4))]
    # Each covers half of the south face: IoU 0.5 for both.
    score = score_structure(faces, [_ring(SOUTH, P_SOUTH)], OUTLINE)
    assert score.angles_deg == pytest.approx((1.0,), abs=0.01)
    assert score.n_unmatched_pred == 1


def test_a_face_counts_for_a_building_holding_a_quarter_of_it() -> None:
    # A semi-detached pair under one face: the face counts for both houses. A sliver
    # of it, under 25%, counts for neither.
    face = RoofFace(0, _plane(18.0, 0.0, 0.0), _box(0, 0, 20, 8))
    west = score_structure([face], [_ring(_box(0, 0, 10, 8), face.plane)], _box(0, 0, 10, 8))
    sliver = score_structure([face], [_ring(_box(0, 0, 3, 8), face.plane)], _box(0, 0, 3, 8))
    assert west.n_pred_faces == 1
    assert sliver.n_pred_faces == 0


def test_only_reconstructed_buildings_get_structure_and_angles_pool() -> None:
    reference = ReferenceBuilding("gable", [RoofPolygon("gable", r) for r in REFERENCE])
    points = np.column_stack([np.full(4, X0 + 5), np.full(4, Y0 + 2), np.full(4, 18.0)])
    ok = FootprintOutcome(
        "fp-a",
        OUTLINE,
        "ok",
        faces=[RoofFace(0, P_SOUTH, SOUTH), RoofFace(1, P_NORTH, NORTH)],
        roof_points=points,
    )
    (row,) = evaluate([ok], [reference])
    assert row.n_pred_faces == 2
    assert row.face_count_agreement == 1.0
    summary = summarise([row])
    assert summary["orientation_error_median_deg"] == pytest.approx(0.0, abs=1e-6)
    assert summary["n_plane_pairs"] == 2

    failed = FootprintOutcome("fp-a", OUTLINE, "solver-failed", roof_points=points)
    (row,) = evaluate([failed], [reference])
    assert row.face_count_agreement is None
    assert row.orientation_errors_deg == ()
    assert summarise([row])["orientation_error_median_deg"] is None
