"""Watertightness checking and the spatial-block splitter."""

from __future__ import annotations

import pytest

from mtl_roofs.geometry.extrude import is_watertight
from mtl_roofs.ml.split import block_id

# A tetrahedron: four triangles, every edge shared by exactly two faces.
TETRAHEDRON = [[0, 1, 2], [0, 2, 3], [0, 3, 1], [1, 3, 2]]


def test_closed_solid_is_watertight() -> None:
    assert is_watertight(TETRAHEDRON)


def test_open_solid_is_not_watertight() -> None:
    assert not is_watertight(TETRAHEDRON[:-1])


def test_empty_face_set_is_not_watertight() -> None:
    assert not is_watertight([])


def test_degenerate_face_is_rejected() -> None:
    assert not is_watertight([[0, 1], [0, 1, 2]])


def test_blocks_are_stable_within_a_cell() -> None:
    assert block_id(292_100.0, 5_034_100.0, 250.0) == block_id(292_249.0, 5_034_249.0, 250.0)


def test_adjacent_buildings_across_a_boundary_fall_in_different_blocks() -> None:
    assert block_id(292_249.0, 5_034_000.0, 250.0) != block_id(292_251.0, 5_034_000.0, 250.0)


def test_block_size_must_be_positive() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        block_id(0.0, 0.0, 0.0)
