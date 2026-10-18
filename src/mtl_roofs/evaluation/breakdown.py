"""The breakdown report: every headline metric by roof type, size and point density (#26).

``docs/evaluation.md`` (Breakdowns) fixes the axes and their bins; the bins are the
constants of :mod:`mtl_roofs.evaluation.report`, so two runs stay comparable. Each
table is computed twice: over every building (**raw**), and **excluding** the
``suspect-temporal`` ones. Buildings on LiDAR-derived footprints, which are not
independent of the input, are also reported on their own.

Every metric sits next to the count of buildings it was computed from, so that a
thin cell is visible as one.
"""

from __future__ import annotations

import csv
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mtl_roofs.evaluation.evaluate import summarise
from mtl_roofs.evaluation.report import BuildingResult, density_bin, size_bin

#: Bin value for a building with no value on an axis (no footprint, so no density).
NO_BIN = "n/a"


@dataclass(frozen=True, slots=True)
class Axis:
    """One breakdown axis: how to bin a row, and the bins in display order."""

    name: str
    bin_of: Callable[[BuildingResult], str]
    bins: tuple[str, ...]


AXES = (
    Axis("roof type", lambda r: r.roof_type or NO_BIN, ("flat", "mixed", "pitched")),
    Axis(
        "size (m²)",
        lambda r: NO_BIN if r.status == "no-reference" else size_bin(r.outline_area_m2),
        ("<100", "100-250", "250-1000", ">=1000"),
    ),
    Axis(
        "point density (pts/m²)",
        lambda r: NO_BIN if r.point_density is None else density_bin(r.point_density),
        ("<5", "5-10", "10-20", ">=20", NO_BIN),
    ),
)

#: Subsets every table is computed over.
VARIANTS: tuple[tuple[str, Callable[[BuildingResult], bool]], ...] = (
    ("raw", lambda r: True),
    ("excluding suspect-temporal", lambda r: r.status != "suspect-temporal"),
)

#: Overall rows of the headline table: the whole set and the footprint provenance split.
GROUPS: tuple[tuple[str, Callable[[BuildingResult], bool]], ...] = (
    ("all buildings", lambda r: True),
    ("independent footprints", lambda r: r.lidar_derived is False),
    ("LiDAR-derived footprints", lambda r: r.lidar_derived is True),
)

#: Columns of every table and of the CSV, with their Markdown label and format.
COLUMNS = (
    ("n_buildings", "buildings", "{:d}"),
    ("n_assigned", "assigned", "{:d}"),
    ("buildings_reconstructed", "reconstructed", "{:d}"),
    ("vertical_rmse_m", "RMSE (m)", "{:.3f}"),
    ("median_building_rmse_m", "median RMSE (m)", "{:.3f}"),
    ("coverage", "coverage", "{:.3f}"),
    ("orientation_error_median_deg", "orient. median (°)", "{:.2f}"),
    ("orientation_error_p90_deg", "orient. p90 (°)", "{:.2f}"),
    ("face_count_agreement", "face agreement", "{:.3f}"),
    ("match_rate", "match rate", "{:.3f}"),
)


def breakdown(rows: Sequence[BuildingResult]) -> list[dict[str, Any]]:
    """Every table of the report as long-format records: one per variant, axis and bin.

    The overall rows have ``axis`` ``"overall"`` and the group name as ``bin``.
    """
    records: list[dict[str, Any]] = []
    for variant, keep in VARIANTS:
        subset = [r for r in rows if keep(r)]
        for group, member in GROUPS:
            records.append(_record(variant, "overall", group, [r for r in subset if member(r)]))
        for axis in AXES:
            for value in axis.bins:
                cell = [r for r in subset if axis.bin_of(r) == value]
                records.append(_record(variant, axis.name, value, cell))
    return records


def write_breakdown(rows: Sequence[BuildingResult], out: Path, title: str) -> tuple[Path, Path]:
    """Write ``breakdown.md`` and ``breakdown.csv`` to ``out``.

    Returns:
        The Markdown and CSV paths.
    """
    records = breakdown(rows)
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / "breakdown.csv"
    with csv_path.open("w", newline="") as handle:
        names = ["variant", "axis", "bin", *(c for c, _, _ in COLUMNS)]
        writer = csv.DictWriter(handle, fieldnames=names, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    md_path = out / "breakdown.md"
    md_path.write_text(_markdown(rows, records, title))
    return md_path, csv_path


def _record(variant: str, axis: str, value: str, rows: list[BuildingResult]) -> dict[str, Any]:
    return {"variant": variant, "axis": axis, "bin": value, **summarise(rows)}


def _markdown(rows: Sequence[BuildingResult], records: list[dict[str, Any]], title: str) -> str:
    statuses = Counter(r.status for r in rows)
    warnings = Counter(w for r in rows for w in r.warnings)
    lines = [
        f"# {title}",
        "",
        "Every metric is a disagreement between the reconstruction (2015 LiDAR) and the "
        "city's 2016 LOD2 model, not an error against ground truth. RMSE and coverage pool "
        "grid cells; definitions in `docs/evaluation.md`.",
        "",
        "## Status of every building and footprint",
        "",
        "| status | rows |",
        "|---|---:|",
        *(f"| `{s}` | {n} |" for s, n in sorted(statuses.items())),
        "",
        "Solver warnings, by type: "
        + (", ".join(f"`{w}` {n}" for w, n in sorted(warnings.items())) or "none")
        + ".",
    ]
    for variant, _ in VARIANTS:
        lines += ["", f"## {variant.capitalize()}", ""]
        lines += _table(
            "", [r for r in records if r["variant"] == variant and r["axis"] == "overall"]
        )
        for axis in AXES:
            cells = [r for r in records if r["variant"] == variant and r["axis"] == axis.name]
            lines += ["", f"### By {axis.name}", ""]
            lines += _table(axis.name, cells)
    return "\n".join(lines) + "\n"


def _table(axis: str, records: list[dict[str, Any]]) -> list[str]:
    header = [axis or "set", *(label for _, label, _ in COLUMNS)]
    lines = [
        "| " + " | ".join(header) + " |",
        "|---|" + "---:|" * len(COLUMNS),
    ]
    for record in records:
        values = [_fmt(fmt, record[key]) for key, _, fmt in COLUMNS]
        lines.append(f"| {record['bin']} | " + " | ".join(values) + " |")
    return lines


def _fmt(fmt: str, value: Any) -> str:
    return "—" if value is None else fmt.format(value)
