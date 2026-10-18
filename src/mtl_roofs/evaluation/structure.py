"""Plane orientation error and face-count agreement (``docs/evaluation.md`` §3-4).

Both compare one reference building with the part of its footprint's reconstruction
that lies in the building's roof outline:

* **Orientation.** Predicted planes are clipped to the outline. Each pair of a
  predicted and a reference plane whose projected areas overlap at IoU of at least
  ``PLANE_IOU`` is a candidate. Candidates are matched greedily, smallest angle first,
  each plane at most once. The error of a pair is the unsigned angle between normals.
* **Face count.** A predicted face counts if at least ``FACE_SHARE`` of its projected
  area lies inside the outline, so a face spanning a party wall counts for both
  buildings and a sliver of the neighbour's face counts for neither. Every reference
  roof polygon counts.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import shapely

from mtl_roofs.evaluation.metrics import face_count_agreement, orientation_error
from mtl_roofs.geometry.roof import RoofFace
from mtl_roofs.io.reference import Point3, newell_normal

#: Smallest projected IoU at which two planes can be matched.
PLANE_IOU = 0.3
#: Smallest share of a predicted face's area inside the outline for it to count.
FACE_SHARE = 0.25


@dataclass(frozen=True, slots=True)
class StructureScore:
    """Orientation and face-count results for one reference building.

    Attributes:
        angles_deg: Orientation error of each matched plane pair.
        n_pred_planes: Predicted planes overlapping the outline.
        n_ref_planes: Reference planes (non-vertical roof polygons).
        n_unmatched_pred: Predicted planes left without a partner.
        n_unmatched_ref: Reference planes left without a partner.
        n_pred_faces: Predicted faces counted for this building.
        n_ref_faces: Reference roof faces.
        face_count_agreement: ``min / max`` of the two face counts.
    """

    angles_deg: tuple[float, ...]
    n_pred_planes: int
    n_ref_planes: int
    n_unmatched_pred: int
    n_unmatched_ref: int
    n_pred_faces: int
    n_ref_faces: int
    face_count_agreement: float


def score_structure(
    faces: Sequence[RoofFace], roofs: Sequence[Sequence[Point3]], outline: Any
) -> StructureScore:
    """Orientation error and face-count agreement of one building.

    Args:
        faces: The assigned footprint's roof partition.
        roofs: The reference building's roof polygons, closed 3D rings.
        outline: The reference building's roof outline.
    """
    shapely.prepare(outline)

    # Predicted planes: each plane's faces, clipped to the outline.
    clipped: dict[int, list[Any]] = {}
    normals: dict[int, tuple[float, float, float]] = {}
    n_pred_faces = 0
    for face in faces:
        inside = shapely.intersection(face.polygon, outline)
        if face.polygon.area > 0 and inside.area >= FACE_SHARE * face.polygon.area:
            n_pred_faces += 1
        if inside.area > 0:
            clipped.setdefault(face.plane_index, []).append(inside)
            normals[face.plane_index] = face.plane.normal
    predicted = [(normals[i], shapely.union_all(parts)) for i, parts in sorted(clipped.items())]

    reference: list[tuple[tuple[float, float, float], Any]] = []
    n_ref_faces = 0
    for ring in roofs:
        try:
            normal = newell_normal(list(ring))
        except ValueError:
            continue
        n_ref_faces += 1
        if abs(normal[2]) < 1e-6:
            continue
        polygon = shapely.make_valid(shapely.Polygon([p[:2] for p in ring]))
        if polygon.area > 0:
            reference.append((normal, polygon))

    candidates = []
    for i, (n_pred, a) in enumerate(predicted):
        for j, (n_ref, b) in enumerate(reference):
            common = shapely.intersection(a, b).area
            union = a.area + b.area - common
            if union > 0 and common / union >= PLANE_IOU:
                candidates.append((orientation_error(n_pred, n_ref), i, j))
    angles: list[float] = []
    used_pred: set[int] = set()
    used_ref: set[int] = set()
    for angle, i, j in sorted(candidates):
        if i not in used_pred and j not in used_ref:
            angles.append(angle)
            used_pred.add(i)
            used_ref.add(j)

    return StructureScore(
        angles_deg=tuple(angles),
        n_pred_planes=len(predicted),
        n_ref_planes=len(reference),
        n_unmatched_pred=len(predicted) - len(used_pred),
        n_unmatched_ref=len(reference) - len(used_ref),
        n_pred_faces=n_pred_faces,
        n_ref_faces=n_ref_faces,
        face_count_agreement=face_count_agreement(n_pred_faces, n_ref_faces),
    )
