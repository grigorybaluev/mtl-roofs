"""Extrusion of a solved roof to a watertight LOD2 solid."""

from __future__ import annotations


def extrude_lod2(*_args: object, **_kwargs: object) -> object:
    """Build a watertight LOD2 solid from roof planes and a footprint.

    Not implemented yet: tracked by the "LOD2 solid extrusion" issue.
    """
    raise NotImplementedError


def is_watertight(faces: list[list[int]]) -> bool:
    """Whether a face set is closed: every undirected edge used exactly twice.

    This is the cheap topological half of the validity check, independent of any
    geometry library, and it is what the extrusion tests assert against.

    Args:
        faces: Each face as a list of vertex indices, not repeating the first vertex.
    """
    edges: dict[tuple[int, int], int] = {}
    for face in faces:
        if len(face) < 3:
            return False
        for i, start in enumerate(face):
            end = face[(i + 1) % len(face)]
            key = (start, end) if start < end else (end, start)
            edges[key] = edges.get(key, 0) + 1
    return bool(edges) and all(count == 2 for count in edges.values())
