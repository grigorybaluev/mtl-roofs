"""The ``eval-smoke`` regression guard: metrics on ``tests/fixtures`` against the baseline.

While every headline metric in ``baseline_metrics.json`` is ``null`` the guard
*reports but does not gate*. It still writes a report saying so, on purpose, so that
a run which evaluated nothing can never be mistaken for one that passed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPORT_NAME = "report.md"


def load_baseline(fixtures: Path) -> dict[str, Any]:
    """Read ``baseline_metrics.json`` from a fixture directory."""
    baseline: dict[str, Any] = json.loads((fixtures / "baseline_metrics.json").read_text())
    return baseline


def baseline_is_set(baseline: dict[str, Any]) -> bool:
    """Whether any overall headline metric has a value, i.e. whether the guard gates."""
    overall = baseline.get("metrics", {}).get("overall", {})
    return any(value is not None for value in overall.values())


def write_pending_report(out: Path, reason: str) -> Path:
    """Write the report for a smoke run that measured nothing, and say why."""
    out.mkdir(parents=True, exist_ok=True)
    path = out / REPORT_NAME
    path.write_text(
        "### eval-smoke\n\n"
        f":construction: **Nothing was measured:** {reason}\n\n"
        "Every metric in `tests/fixtures/baseline_metrics.json` is `null`, so this "
        "guard reports but does not gate. It starts gating once the baseline has "
        "real numbers.\n"
    )
    return path
