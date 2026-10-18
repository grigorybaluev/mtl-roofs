"""Metrics, building matching and per-building reporting.

Metric definitions live in ``docs/evaluation.md`` and are frozen: changing one is a
separate pull request from any algorithm change. See CLAUDE.md.
"""

from mtl_roofs.evaluation.evaluate import FootprintOutcome, evaluate, summarise
from mtl_roofs.evaluation.matching import Assignment, assign_buildings, iou
from mtl_roofs.evaluation.metrics import orientation_error, vertical_rmse

__all__ = [
    "Assignment",
    "FootprintOutcome",
    "assign_buildings",
    "evaluate",
    "iou",
    "orientation_error",
    "summarise",
    "vertical_rmse",
]
