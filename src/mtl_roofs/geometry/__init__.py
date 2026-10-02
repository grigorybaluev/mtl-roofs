"""Roof reconstruction: clipping, plane detection, topology, extrusion to a LOD2 solid."""

from mtl_roofs.geometry.clip import ClipParams, ClipResult, ClipStatus, clip_footprint
from mtl_roofs.geometry.planes import (
    Plane,
    PlaneDetection,
    PlaneParams,
    PlaneStatus,
    detect_planes,
    fit_plane,
    ransac_planes,
)
from mtl_roofs.geometry.topology import (
    FailureReason,
    TopologyParams,
    TopologyResult,
    TopologyStatus,
    solve_topology,
)

__all__ = [
    "ClipParams",
    "ClipResult",
    "ClipStatus",
    "FailureReason",
    "Plane",
    "PlaneDetection",
    "PlaneParams",
    "PlaneStatus",
    "TopologyParams",
    "TopologyResult",
    "TopologyStatus",
    "clip_footprint",
    "detect_planes",
    "fit_plane",
    "ransac_planes",
    "solve_topology",
]
