"""Scoring reconstructions against the reference model, per building and overall.

The definitions are ``docs/evaluation.md``; this module follows §6 of it. In brief:
every non-grouped reference building is assigned to a footprint (or is
``no-match``), scored on the 0.5 m grid inside its eroded roof outline against that
footprint's reconstruction, and flagged ``suspect-temporal`` from the LiDAR. Overall
RMSE and coverage pool the cells of all assigned buildings.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from mtl_roofs.evaluation.grid import (
    building_cells,
    lidar_surface,
    predicted_surface,
    reference_surface,
)
from mtl_roofs.evaluation.matching import assign_buildings
from mtl_roofs.evaluation.report import BuildingResult
from mtl_roofs.evaluation.structure import score_structure
from mtl_roofs.geometry.roof import RoofFace
from mtl_roofs.io.reference import ReferenceBuilding, roof_outline

FloatArray = npt.NDArray[np.float64]

#: A building whose LiDAR sits further than this from the reference, as a median, is
#: ``suspect-temporal``.
TEMPORAL_THRESHOLD_M = 1.5

#: Statuses of reference buildings with no footprint to score against.
UNASSIGNED = frozenset({"no-match", "grouped-reference"})
#: Statuses of buildings whose footprint reconstructed.
RECONSTRUCTED = frozenset({"ok", "suspect-temporal"})


@dataclass(frozen=True, slots=True)
class FootprintOutcome:
    """What the evaluation needs from one footprint's reconstruction.

    Attributes:
        footprint_id: The footprint's id.
        polygon: The footprint, EPSG:2950.
        status: Its reconstruction status.
        faces: Its roof partition; only read when ``status`` is ``ok``.
        roof_points: ``(n, 3)`` retained roof points, for the temporal flag.
        density: Retained roof points per m² of footprint.
        lidar_derived: Whether the footprint was derived from the 2015 LiDAR.
        reason: The reconstruction's failure reason.
        warnings: The topology solver's warnings.
    """

    footprint_id: str
    polygon: Any
    status: str
    faces: Sequence[RoofFace] = ()
    roof_points: FloatArray = field(default_factory=lambda: np.empty((0, 3)))
    density: float = 0.0
    lidar_derived: bool = False
    reason: str = ""
    warnings: tuple[str, ...] = ()


def evaluate(
    footprints: Sequence[FootprintOutcome], buildings: Iterable[ReferenceBuilding]
) -> list[BuildingResult]:
    """Score every reference building, and list every footprint with none.

    Returns:
        One row per reference building, then one ``no-reference`` row per footprint
        that no reference building was assigned to.
    """
    by_id = {f.footprint_id: f for f in footprints}
    buildings = list(buildings)
    outlines = {b.gml_id: roof_outline(b) for b in buildings}
    assigned = assign_buildings(
        {f.footprint_id: f.polygon for f in footprints},
        {b.gml_id: outlines[b.gml_id] for b in buildings if not b.is_grouped},
    )

    rows: list[BuildingResult] = []
    for building in buildings:
        outline = outlines[building.gml_id]
        assignment = None if building.is_grouped else assigned[building.gml_id]
        if assignment is None:
            status = "grouped-reference" if building.is_grouped else "no-match"
            rows.append(
                BuildingResult(
                    building_id=building.gml_id,
                    footprint_id=None,
                    status=status,
                    roof_type=building.roof_type(),
                    outline_area_m2=float(outline.area),
                )
            )
            continue
        footprint = by_id[assignment.footprint_id]
        rows.append(score_building(building, outline, footprint, assignment.share))

    used = {r.footprint_id for r in rows}
    rows += [
        BuildingResult(
            building_id=f.footprint_id,
            footprint_id=f.footprint_id,
            status="no-reference",
            point_density=f.density,
            lidar_derived=f.lidar_derived,
            reason=f.reason,
            warnings=f.warnings,
        )
        for f in footprints
        if f.footprint_id not in used
    ]
    return rows


def score_building(
    building: ReferenceBuilding, outline: Any, footprint: FootprintOutcome, share: float
) -> BuildingResult:
    """Score one assigned reference building against its footprint's reconstruction."""
    x, y = building_cells(outline)
    z_ref = reference_surface([r.points for r in building.roofs], x, y)
    if footprint.status == "ok":
        z_pred = predicted_surface(footprint.faces, x, y)
    else:
        z_pred = np.full(len(x), np.nan)

    has_ref = np.isfinite(z_ref)
    valid = has_ref & np.isfinite(z_pred)
    n_ref, n_valid = int(has_ref.sum()), int(valid.sum())
    sum_sq = float(np.sum((z_pred[valid] - z_ref[valid]) ** 2))

    z_lidar = lidar_surface(footprint.roof_points, x, y)
    seen = has_ref & np.isfinite(z_lidar)
    offset = float(np.median(z_lidar[seen] - z_ref[seen])) if seen.any() else None

    status = footprint.status
    if status == "ok" and offset is not None and abs(offset) > TEMPORAL_THRESHOLD_M:
        status = "suspect-temporal"
    structure: dict[str, Any] = {}
    if footprint.status == "ok":
        score = score_structure(footprint.faces, [r.points for r in building.roofs], outline)
        structure = {
            "orientation_errors_deg": score.angles_deg,
            "orientation_median_deg": (
                float(np.median(score.angles_deg)) if score.angles_deg else None
            ),
            "n_pred_planes": score.n_pred_planes,
            "n_ref_planes": score.n_ref_planes,
            "n_unmatched_pred": score.n_unmatched_pred,
            "n_unmatched_ref": score.n_unmatched_ref,
            "n_pred_faces": score.n_pred_faces,
            "n_ref_faces": score.n_ref_faces,
            "face_count_agreement": score.face_count_agreement,
        }
    return BuildingResult(
        building_id=building.gml_id,
        footprint_id=footprint.footprint_id,
        status=status,
        roof_type=building.roof_type(),
        outline_area_m2=float(outline.area),
        share=share,
        point_density=footprint.density,
        lidar_derived=footprint.lidar_derived,
        n_ref_cells=n_ref,
        n_valid_cells=n_valid,
        sum_sq_m2=sum_sq,
        rmse_m=float(np.sqrt(sum_sq / n_valid)) if n_valid else None,
        coverage=n_valid / n_ref if n_ref else None,
        temporal_offset_m=offset,
        reason=footprint.reason,
        warnings=footprint.warnings,
        **structure,
    )


def summarise(rows: Sequence[BuildingResult]) -> dict[str, Any]:
    """Headline metrics over a set of rows (``docs/evaluation.md`` §6).

    Rows of any status may be passed; each metric picks the ones it is defined over.
    A metric with nothing to be computed from is ``None``.
    """
    reference = [r for r in rows if r.status != "no-reference"]
    candidates = [r for r in reference if r.status != "grouped-reference"]
    assigned = [r for r in candidates if r.status not in UNASSIGNED]
    reconstructed = [r for r in assigned if r.status in RECONSTRUCTED]

    n_valid = sum(r.n_valid_cells for r in assigned)
    n_ref = sum(r.n_ref_cells for r in assigned)
    per_building = [r.rmse_m for r in assigned if r.rmse_m is not None]
    shares = [r.share for r in assigned if r.share is not None]
    per_footprint = Counter(r.footprint_id for r in assigned)
    angles = [a for r in reconstructed for a in r.orientation_errors_deg]
    agreement = [
        r.face_count_agreement for r in reconstructed if r.face_count_agreement is not None
    ]

    def total(name: str) -> int:
        return sum(getattr(r, name) or 0 for r in reconstructed)

    return {
        "vertical_rmse_m": (
            float(np.sqrt(sum(r.sum_sq_m2 for r in assigned) / n_valid)) if n_valid else None
        ),
        "median_building_rmse_m": float(np.median(per_building)) if per_building else None,
        "coverage": n_valid / n_ref if n_ref else None,
        "orientation_error_median_deg": float(np.median(angles)) if angles else None,
        "orientation_error_p90_deg": float(np.quantile(angles, 0.9)) if angles else None,
        "face_count_agreement": float(np.mean(agreement)) if agreement else None,
        "match_rate": len(assigned) / len(candidates) if candidates else None,
        "buildings_reconstructed": len(reconstructed),
        "n_buildings": len(reference),
        "n_assigned": len(assigned),
        "n_grouped": len(reference) - len(candidates),
        "n_no_reference": len(rows) - len(reference),
        "n_plane_pairs": len(angles),
        "n_unmatched_pred_planes": total("n_unmatched_pred"),
        "n_unmatched_ref_planes": total("n_unmatched_ref"),
        "n_pred_faces": total("n_pred_faces"),
        "n_ref_faces": total("n_ref_faces"),
        "share_quantiles": (
            [float(q) for q in np.quantile(shares, [0.0, 0.1, 0.5])] if shares else None
        ),
        "buildings_per_footprint": dict(sorted(Counter(per_footprint.values()).items())),
        "status_counts": dict(sorted(Counter(r.status for r in rows).items())),
    }
