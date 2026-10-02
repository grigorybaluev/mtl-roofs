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

__all__ = [
    "ClipParams",
    "ClipResult",
    "ClipStatus",
    "Plane",
    "PlaneDetection",
    "PlaneParams",
    "PlaneStatus",
    "clip_footprint",
    "detect_planes",
    "fit_plane",
    "ransac_planes",
]
