"""Metric definitions. These are frozen; see docs/evaluation.md."""

from __future__ import annotations

import numpy as np
import pytest

from mtl_roofs.evaluation.metrics import (
    coverage,
    face_count_agreement,
    orientation_error,
    vertical_rmse,
)


def test_vertical_rmse_of_identical_grids_is_zero() -> None:
    grid = np.array([[1.0, 2.0], [3.0, 4.0]])
    assert vertical_rmse(grid, grid) == 0.0


def test_vertical_rmse_is_the_quadratic_mean() -> None:
    pred = np.array([[0.0, 0.0], [0.0, 0.0]])
    ref = np.array([[3.0, 4.0], [0.0, 0.0]])
    assert vertical_rmse(pred, ref) == pytest.approx(np.sqrt(25.0 / 4))


def test_vertical_rmse_ignores_cells_missing_from_either_grid() -> None:
    pred = np.array([[1.0, np.nan], [2.0, 5.0]])
    ref = np.array([[1.0, 100.0], [np.nan, 5.0]])
    assert vertical_rmse(pred, ref) == 0.0


def test_vertical_rmse_needs_an_overlap() -> None:
    pred = np.array([[np.nan]])
    ref = np.array([[1.0]])
    with pytest.raises(ValueError, match="no cell is present in both"):
        vertical_rmse(pred, ref)


def test_coverage_exposes_a_sparse_reconstruction() -> None:
    """A reconstruction covering a quarter of the roof must not look complete."""
    pred = np.array([[1.0, np.nan], [np.nan, np.nan]])
    ref = np.ones((2, 2))
    assert vertical_rmse(pred, ref) == 0.0
    assert coverage(pred, ref) == 0.25


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ((0, 0, 1), (0, 0, 1), 0.0),
        ((0, 0, 1), (0, 0, -1), 0.0),
        ((0, 0, 1), (1, 0, 0), 90.0),
        ((0, 0, 1), (0, 1, 1), 45.0),
    ],
)
def test_orientation_error_is_unsigned(
    a: tuple[float, float, float], b: tuple[float, float, float], expected: float
) -> None:
    assert orientation_error(a, b) == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize(
    ("pred", "ref", "expected"),
    [(4, 4, 1.0), (2, 4, 0.5), (4, 2, 0.5), (0, 0, 1.0), (0, 3, 0.0)],
)
def test_face_count_agreement_is_symmetric(pred: int, ref: int, expected: float) -> None:
    assert face_count_agreement(pred, ref) == pytest.approx(expected)
