"""Spatial matching, needed because the footprints carry no building id."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from mtl_roofs.evaluation.matching import iou


def test_identical_polygons_have_iou_one() -> None:
    assert iou(100.0, 100.0, 100.0) == pytest.approx(1.0)


def test_disjoint_polygons_have_iou_zero() -> None:
    assert iou(100.0, 50.0, 0.0) == 0.0


def test_half_overlap() -> None:
    assert iou(100.0, 100.0, 50.0) == pytest.approx(50.0 / 150.0)


def test_intersection_cannot_exceed_a_constituent() -> None:
    with pytest.raises(ValueError, match="exceeds a constituent"):
        iou(10.0, 10.0, 11.0)


@given(
    a=st.floats(1.0, 1e6),
    b=st.floats(1.0, 1e6),
    fraction=st.floats(0.0, 1.0),
)
def test_property_iou_is_bounded_and_symmetric(a: float, b: float, fraction: float) -> None:
    intersection = fraction * min(a, b)
    value = iou(a, b, intersection)
    assert 0.0 <= value <= 1.0 + 1e-9
    assert value == pytest.approx(iou(b, a, intersection))
