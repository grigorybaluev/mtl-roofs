"""Per-building results and the fixed bins of the breakdown report."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BuildingResult:
    """One row of the per-building results table: every building gets one.

    Reference buildings are keyed by ``gml:id``; a footprint with no reference
    building assigned gets a ``no-reference`` row keyed by its footprint id.

    Attributes:
        building_id: ``gml:id``, or the footprint id for a ``no-reference`` row.
        footprint_id: The assigned footprint; ``None`` for ``no-match`` and
            ``grouped-reference``.
        status: Failure-log status (``docs/evaluation.md``).
        roof_type: ``flat``, ``mixed`` or ``pitched`` from the reference model.
        outline_area_m2: Reference roof-outline area (the size breakdown axis).
        share: Share of the outline inside the assigned footprint.
        point_density: Roof points per m² of the assigned footprint.
        lidar_derived: Whether the assigned footprint was derived from the LiDAR.
        n_ref_cells: Grid cells where the reference surface is defined.
        n_valid_cells: Of those, cells where the prediction is defined too.
        sum_sq_m2: Sum of squared height differences over the valid cells.
        rmse_m: Vertical RMSE over the valid cells; ``None`` with no valid cell.
        coverage: ``n_valid_cells / n_ref_cells``; ``None`` with no reference cell.
        temporal_offset_m: Median LiDAR-minus-reference height over the cells.
        orientation_errors_deg: Orientation error of each matched plane pair.
        orientation_median_deg: Their median; ``None`` with no matched pair.
        n_pred_planes: Predicted planes overlapping the outline.
        n_ref_planes: Reference planes.
        n_unmatched_pred: Predicted planes with no reference partner.
        n_unmatched_ref: Reference planes with no predicted partner.
        n_pred_faces: Predicted faces counted for the building.
        n_ref_faces: Reference roof faces.
        face_count_agreement: ``min / max`` of the face counts.
        reason: The reconstruction's failure reason, if any.
        warnings: The topology solver's warnings for the footprint.
    """

    building_id: str
    footprint_id: str | None
    status: str
    roof_type: str | None = None
    outline_area_m2: float = 0.0
    share: float | None = None
    point_density: float | None = None
    lidar_derived: bool | None = None
    n_ref_cells: int = 0
    n_valid_cells: int = 0
    sum_sq_m2: float = 0.0
    rmse_m: float | None = None
    coverage: float | None = None
    temporal_offset_m: float | None = None
    orientation_errors_deg: tuple[float, ...] = ()
    orientation_median_deg: float | None = None
    n_pred_planes: int | None = None
    n_ref_planes: int | None = None
    n_unmatched_pred: int | None = None
    n_unmatched_ref: int | None = None
    n_pred_faces: int | None = None
    n_ref_faces: int | None = None
    face_count_agreement: float | None = None
    reason: str = ""
    warnings: tuple[str, ...] = ()


def density_bin(density: float) -> str:
    """Bucket a point density (points/m²) for the breakdown report.

    Bins are fixed here rather than derived from the run so that the breakdown is
    comparable between runs and between the classical and ML pipelines.
    """
    if density < 5:
        return "<5"
    if density < 10:
        return "5-10"
    if density < 20:
        return "10-20"
    return ">=20"


def size_bin(area_m2: float) -> str:
    """Bucket a reference roof-outline area (m²) for the breakdown report."""
    if area_m2 < 100:
        return "<100"
    if area_m2 < 250:
        return "100-250"
    if area_m2 < 1000:
        return "250-1000"
    return ">=1000"
