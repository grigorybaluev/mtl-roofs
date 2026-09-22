"""Metrics, building matching and per-building reporting.

Metric definitions live in ``docs/evaluation.md`` and are frozen: changing one is a
separate pull request from any algorithm change. See CLAUDE.md.
"""

from mtl_roofs.evaluation.matching import iou
from mtl_roofs.evaluation.metrics import orientation_error, vertical_rmse

__all__ = ["iou", "orientation_error", "vertical_rmse"]
