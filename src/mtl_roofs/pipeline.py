"""Reconstruction of one footprint, end to end.

``clip → detect planes → solve topology → partition the roof → extrude``. Each stage
can stop the footprint with one of the failure-log statuses of
``docs/evaluation.md``; the stage that stopped it is visible from the status, and
everything computed before it is kept, so a failure can be diagnosed from its row.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import shapely

from mtl_roofs.geometry.clip import GROUND_CLASS, ClipParams, ClipStatus, clip_footprint
from mtl_roofs.geometry.extrude import Extrusion, ExtrusionParams, ExtrusionStatus, extrude_lod2
from mtl_roofs.geometry.planes import PlaneParams, PlaneStatus, detect_planes
from mtl_roofs.geometry.roof import RoofFace, RoofParams, partition_roof
from mtl_roofs.geometry.topology import (
    TopologyParams,
    TopologyResult,
    TopologyStatus,
    solve_topology,
)
from mtl_roofs.io.lidar import PointSet

log = logging.getLogger(__name__)


class Status(Enum):
    """Reconstruction status of a footprint, as in the failure log."""

    OK = "ok"
    TOO_FEW_POINTS = "too-few-points"
    NO_PLANES = "no-planes"
    SOLVER_FAILED = "solver-failed"
    NOT_WATERTIGHT = "not-watertight"


@dataclass(frozen=True, slots=True)
class PipelineParams:
    """Parameters of every stage."""

    clip: ClipParams = field(default_factory=ClipParams)
    planes: PlaneParams = field(default_factory=PlaneParams)
    topology: TopologyParams = field(default_factory=TopologyParams)
    roof: RoofParams = field(default_factory=RoofParams)
    extrusion: ExtrusionParams = field(default_factory=ExtrusionParams)


@dataclass(frozen=True, slots=True)
class Reconstruction:
    """What happened to one footprint.

    Attributes:
        status: The failure-log status.
        reason: Why, for ``solver-failed`` (the solver's reason) and
            ``not-watertight`` (the failed checks); empty otherwise.
        warnings: The topology solver's warnings, by kind.
        n_points: Roof points retained by the clipper.
        density: Roof points per m² of footprint.
        ground_z: Floor height of the solid.
        topology: The solved planes, once that stage ran.
        faces: The roof partition; empty unless the solver succeeded.
        extrusion: The solid and its checks; ``None`` unless the solver succeeded.
    """

    status: Status
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    n_points: int = 0
    density: float = 0.0
    ground_z: float = float("nan")
    topology: TopologyResult | None = None
    faces: list[RoofFace] = field(default_factory=list)
    extrusion: Extrusion | None = None


def ground_height(points: PointSet) -> float:
    """Floor height for a footprint: the median of its ground-class points.

    The points around a footprint include a margin of context (2 m in the fixtures),
    which is where the ground returns are. With none at all, the lowest point is
    used instead.
    """
    ground = points.xyz[points.classification == GROUND_CLASS, 2]
    if len(ground):
        return float(np.median(ground))
    return float(points.xyz[:, 2].min()) if len(points) else float("nan")


def reconstruct_footprint(
    points: PointSet, footprint: shapely.Polygon, params: PipelineParams | None = None
) -> Reconstruction:
    """Reconstruct one footprint's LOD2 solid.

    Args:
        points: LiDAR around the footprint, including ground in a margin around it.
        footprint: The ``CARTO-BAT-TOIT`` polygon, in EPSG:2950.
        params: Defaults to :class:`PipelineParams`.
    """
    p = params or PipelineParams()
    clipped = clip_footprint(points, footprint, p.clip)

    def result(status: Status, **kwargs: object) -> Reconstruction:
        return Reconstruction(
            status,
            n_points=clipped.n_points,
            density=clipped.density,
            ground_z=ground_height(points),
            **kwargs,  # type: ignore[arg-type]
        )

    if clipped.status is ClipStatus.TOO_FEW_POINTS:
        return result(Status.TOO_FEW_POINTS)

    pts = clipped.roof.xyz
    detection = detect_planes(pts, p.planes)
    if detection.status is PlaneStatus.NO_PLANES:
        return result(Status.NO_PLANES)

    topology = solve_topology(pts, detection.planes, footprint, p.topology)
    warnings = sorted(w.kind.value for w in topology.warnings)
    if topology.status is TopologyStatus.SOLVER_FAILED:
        reason = topology.reason.value if topology.reason else ""
        return result(Status.SOLVER_FAILED, reason=reason, warnings=warnings, topology=topology)

    faces = partition_roof(footprint, pts, topology.planes, p.roof)
    extrusion = extrude_lod2(faces, ground_height(points), p.extrusion)
    if extrusion.status is ExtrusionStatus.NOT_WATERTIGHT:
        log.warning("not watertight: %s", "; ".join(extrusion.problems))
        status, reason = Status.NOT_WATERTIGHT, "; ".join(extrusion.problems)
    else:
        status, reason = Status.OK, ""
    return result(
        status,
        reason=reason,
        warnings=warnings,
        topology=topology,
        faces=faces,
        extrusion=extrusion,
    )
