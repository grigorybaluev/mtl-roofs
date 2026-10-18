"""Roof reconstruction: clipping, plane detection, topology, extrusion to a LOD2 solid."""

from mtl_roofs.geometry.clip import ClipParams, ClipResult, ClipStatus, clip_footprint
from mtl_roofs.geometry.extrude import (
    Extrusion,
    ExtrusionParams,
    ExtrusionStatus,
    Solid,
    Surface,
    SurfaceKind,
    extrude_lod2,
)
from mtl_roofs.geometry.planes import (
    Plane,
    PlaneDetection,
    PlaneParams,
    PlaneStatus,
    detect_planes,
    fit_plane,
    ransac_planes,
)
from mtl_roofs.geometry.roof import RoofFace, RoofParams, partition_roof
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
    "Extrusion",
    "ExtrusionParams",
    "ExtrusionStatus",
    "FailureReason",
    "Plane",
    "PlaneDetection",
    "PlaneParams",
    "PlaneStatus",
    "RoofFace",
    "RoofParams",
    "Solid",
    "Surface",
    "SurfaceKind",
    "TopologyParams",
    "TopologyResult",
    "TopologyStatus",
    "clip_footprint",
    "detect_planes",
    "extrude_lod2",
    "fit_plane",
    "partition_roof",
    "ransac_planes",
    "solve_topology",
]
