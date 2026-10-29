"""The viewer scene's geometry encoding (#28)."""

from __future__ import annotations

import numpy as np
import pytest

from mtl_roofs.export.scene import Quantiser, _Writer, triangulate


def _area(tris: np.ndarray) -> float:
    return float(
        0.5
        * np.linalg.norm(np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]), axis=1).sum()
    )


def test_triangulates_a_sloped_roof_face_exactly() -> None:
    # a 10 x 4 m face rising 3 m across its width: area 10 * 5
    ring = np.array([[0, 0, 10], [10, 0, 10], [10, 4, 13], [0, 4, 13], [0, 0, 10]], float)
    tris = triangulate([ring])
    assert _area(tris) == pytest.approx(50.0)
    corners = {tuple(p) for p in tris.reshape(-1, 3)}
    assert corners <= {tuple(p) for p in ring}


def test_vertical_wall_projects_onto_its_own_plane() -> None:
    wall = np.array([[0, 0, 0], [6, 0, 0], [6, 0, 3], [0, 0, 3]], float)
    assert _area(triangulate([wall])) == pytest.approx(18.0)


def test_holes_are_left_open() -> None:
    shell = np.array([[0, 0, 5], [10, 0, 5], [10, 10, 5], [0, 10, 5]], float)
    hole = np.array([[4, 4, 5], [4, 6, 5], [6, 6, 5], [6, 4, 5]], float)
    assert _area(triangulate([shell, hole])) == pytest.approx(96.0)


def test_concave_face_is_not_filled_in() -> None:
    ell = np.array([[0, 0, 1], [4, 0, 1], [4, 1, 1], [1, 1, 1], [1, 4, 1], [0, 4, 1]], float)
    assert _area(triangulate([ell])) == pytest.approx(7.0)


def test_repaired_polygon_keeps_its_vertices_on_the_plane() -> None:
    # a bow-tie: make_valid splits it at a crossing point that is not an input vertex
    bow = np.array([[0, 0, 0], [2, 2, 2], [2, 0, 2], [0, 2, 0]], float)
    tris = triangulate([bow])
    assert len(tris) >= 2
    pts = tris.reshape(-1, 3)
    np.testing.assert_allclose(pts[:, 2], pts[:, 0])  # the plane z = x


def test_degenerate_ring_gives_no_triangles() -> None:
    assert triangulate([np.array([[0, 0, 0], [1, 1, 1]], float)]).shape == (0, 3, 3)


def test_quantiser_round_trips_within_half_a_step() -> None:
    lo, hi = np.array([292_000.0, 5_034_000.0, 20.0]), np.array([293_600.0, 5_035_200.0, 90.0])
    q = Quantiser.covering(lo, hi)
    xyz = np.random.default_rng(0).uniform(lo, hi, size=(1000, 3))
    assert np.abs(q.decode(q.encode(xyz)) - xyz).max() <= q.scale / 2 + 1e-9
    assert q.scale < 0.03


def test_quantiser_refuses_points_off_its_grid() -> None:
    q = Quantiser((0.0, 0.0, 0.0), 0.01)
    with pytest.raises(ValueError, match="outside"):
        q.encode(np.array([[700.0, 0.0, 0.0]]))


def test_writer_aligns_every_array_to_four_bytes() -> None:
    w = _Writer()
    w.add("a", np.arange(3, dtype=np.uint8))
    w.add("b", np.arange(5, dtype=np.uint16))
    buf = b"".join(w.chunks)
    assert w.index["a"] == {"dtype": "uint8", "offset": 0, "length": 3}
    assert w.index["b"]["offset"] == 4
    b = np.frombuffer(buf, "<u2", count=5, offset=w.index["b"]["offset"])
    np.testing.assert_array_equal(b, np.arange(5))
