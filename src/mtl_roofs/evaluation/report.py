"""Per-building results and the breakdown report."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BuildingResult:
    """One row of the per-building results table."""

    building_id: str
    matched: bool
    roof_type: str
    n_points: int
    point_density: float
    footprint_area: float
    rmse: float | None = None
    coverage: float | None = None
    mean_orientation_error: float | None = None
    predicted_faces: int | None = None
    reference_faces: int | None = None
    status: str = "ok"
    note: str = ""


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
    """Bucket a footprint area (m²) for the breakdown report."""
    if area_m2 < 100:
        return "<100"
    if area_m2 < 250:
        return "100-250"
    if area_m2 < 1000:
        return "250-1000"
    return ">=1000"


def write_breakdown(*_args: object, **_kwargs: object) -> None:
    """Write the Markdown + CSV breakdown report.

    Not implemented yet: tracked by the "Breakdown report" issue.
    """
    raise NotImplementedError
