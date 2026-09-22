"""Headline metrics.

Every definition here is mirrored in ``docs/evaluation.md``. If you change one, the
document and ``tests/fixtures/baseline_metrics.json`` change in the same pull
request, and that pull request changes nothing else.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]


def vertical_rmse(predicted: FloatArray, reference: FloatArray) -> float:
    """Root-mean-square vertical difference between two rasterised height grids.

    Both grids must already be sampled on the same grid, in metres, with ``NaN``
    marking cells absent from either surface. Cells missing from either input are
    excluded, and the count of excluded cells is reported separately by
    :func:`coverage` - an implementation that silently dropped them could score well
    by reconstructing almost nothing.

    Raises:
        ValueError: if the grids differ in shape, or no cell is common to both.
    """
    pred = np.asarray(predicted, dtype=np.float64)
    ref = np.asarray(reference, dtype=np.float64)
    if pred.shape != ref.shape:
        msg = f"grid shapes differ: {pred.shape} vs {ref.shape}"
        raise ValueError(msg)
    valid = np.isfinite(pred) & np.isfinite(ref)
    if not valid.any():
        msg = "no cell is present in both grids; RMSE is undefined"
        raise ValueError(msg)
    diff = pred[valid] - ref[valid]
    return float(np.sqrt(np.mean(diff**2)))


def coverage(predicted: FloatArray, reference: FloatArray) -> float:
    """Fraction of reference cells for which a prediction exists, in ``[0, 1]``.

    Reported alongside :func:`vertical_rmse` so that a low RMSE earned by
    reconstructing a small fraction of a roof is visible rather than flattering.
    """
    pred = np.asarray(predicted, dtype=np.float64)
    ref = np.asarray(reference, dtype=np.float64)
    if pred.shape != ref.shape:
        msg = f"grid shapes differ: {pred.shape} vs {ref.shape}"
        raise ValueError(msg)
    ref_valid = np.isfinite(ref)
    total = int(ref_valid.sum())
    if total == 0:
        return 0.0
    return float((ref_valid & np.isfinite(pred)).sum() / total)


def orientation_error(
    predicted_normal: tuple[float, float, float],
    reference_normal: tuple[float, float, float],
) -> float:
    """Angle between two plane normals in degrees, in ``[0, 90]``.

    Normals are compared unsigned, because a roof plane's outward direction is a
    convention of whichever writer produced it and carries no information here.
    """
    pred = np.asarray(predicted_normal, dtype=np.float64)
    ref = np.asarray(reference_normal, dtype=np.float64)
    pred_norm = float(np.linalg.norm(pred))
    ref_norm = float(np.linalg.norm(ref))
    if pred_norm == 0.0 or ref_norm == 0.0:
        msg = "cannot measure the orientation of a zero-length normal"
        raise ValueError(msg)
    cosine = abs(float(np.dot(pred / pred_norm, ref / ref_norm)))
    return float(np.degrees(np.arccos(min(1.0, cosine))))


def face_count_agreement(predicted_faces: int, reference_faces: int) -> float:
    """Symmetric agreement between two face counts, in ``[0, 1]``.

    ``1.0`` means identical counts; the measure is symmetric so that over- and
    under-segmentation by the same factor score the same.
    """
    if predicted_faces < 0 or reference_faces < 0:
        msg = "face counts must be non-negative"
        raise ValueError(msg)
    if predicted_faces == 0 and reference_faces == 0:
        return 1.0
    return min(predicted_faces, reference_faces) / max(predicted_faces, reference_faces)
