"""The ``eval-smoke`` regression guard: metrics on ``tests/fixtures`` against the baseline.

The pipeline is run on every fixture footprint, scored against the fixtures'
reference buildings, and compared with ``baseline_metrics.json``. A headline metric
that moves beyond its tolerance in the bad direction is a regression
(``docs/evaluation.md``, Regression guard). While every baseline value is ``null`` the
guard reports but does not gate.

Outputs, in the ``--out`` directory: ``report.md`` (posted on the pull request),
``metrics.json`` and ``buildings.csv``.
"""

from __future__ import annotations

import csv
import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import shapely

from mtl_roofs.evaluation.evaluate import FootprintOutcome, evaluate, summarise
from mtl_roofs.evaluation.report import BuildingResult
from mtl_roofs.io.lidar import read_points
from mtl_roofs.io.reference import ReferenceBuilding, RoofPolygon
from mtl_roofs.pipeline import PipelineParams, reconstruct_footprint

REPORT_NAME = "report.md"

#: Metrics where a rise is a regression; for every other gated metric a fall is.
LOWER_IS_BETTER = frozenset({"vertical_rmse_m", "orientation_error_median_deg"})

#: Rows of the report table, in order, with their label and number format.
REPORTED = (
    ("vertical_rmse_m", "vertical RMSE (m)", "{:.3f}"),
    ("median_building_rmse_m", "median per-building RMSE (m)", "{:.3f}"),
    ("coverage", "coverage", "{:.3f}"),
    ("orientation_error_median_deg", "orientation error, median (°)", "{:.2f}"),
    ("orientation_error_p90_deg", "orientation error, p90 (°)", "{:.2f}"),
    ("face_count_agreement", "face-count agreement", "{:.3f}"),
    ("match_rate", "match rate", "{:.3f}"),
    ("buildings_reconstructed", "buildings reconstructed", "{:d}"),
)


@dataclass(frozen=True, slots=True)
class SmokeResult:
    """Outcome of a smoke run.

    Attributes:
        overall: Headline metrics of this run.
        regressions: One message per gated metric that regressed or is missing.
        gating: Whether the baseline is set, so that regressions fail the run.
    """

    overall: dict[str, Any]
    regressions: list[str]
    gating: bool

    @property
    def failed(self) -> bool:
        """Whether eval-smoke should fail."""
        return self.gating and bool(self.regressions)


def load_baseline(fixtures: Path) -> dict[str, Any]:
    """Read ``baseline_metrics.json`` from a fixture directory."""
    baseline: dict[str, Any] = json.loads((fixtures / "baseline_metrics.json").read_text())
    return baseline


def baseline_is_set(baseline: dict[str, Any]) -> bool:
    """Whether any overall headline metric has a value, i.e. whether the guard gates."""
    overall = baseline.get("metrics", {}).get("overall", {})
    return any(value is not None for value in overall.values())


def compare(baseline: dict[str, Any], overall: dict[str, Any]) -> list[str]:
    """Gated metrics that regressed beyond tolerance, or that this run did not produce."""
    tolerances = baseline.get("tolerances", {})
    expected = baseline.get("metrics", {}).get("overall", {})
    regressions = []
    for name, tolerance in tolerances.items():
        if name.startswith("_") or expected.get(name) is None:
            continue
        before, after = expected[name], overall.get(name)
        if after is None:
            regressions.append(f"`{name}` was not produced (baseline {before})")
            continue
        change = after - before
        if name in LOWER_IS_BETTER:
            worse = change > tolerance + 1e-12
        else:
            worse = change < -abs(tolerance) - 1e-12
        if worse:
            regressions.append(f"`{name}` moved from {before} to {after} (tolerance {tolerance})")
    return regressions


def run_smoke(fixtures: Path, out: Path, params: PipelineParams | None = None) -> SmokeResult:
    """Reconstruct and score the fixtures, compare with the baseline, write the outputs."""
    baseline = load_baseline(fixtures)
    footprints, buildings = load_fixtures(fixtures, params)
    rows = evaluate(footprints, buildings)
    overall = summarise(rows)
    by_type = {
        roof_type: summarise([r for r in rows if r.roof_type == roof_type])
        for roof_type in ("flat", "mixed", "pitched")
    }
    gating = baseline_is_set(baseline)
    result = SmokeResult(overall, compare(baseline, overall) if gating else [], gating)

    out.mkdir(parents=True, exist_ok=True)
    (out / "metrics.json").write_text(
        json.dumps({"overall": overall, "by_roof_type": by_type}, indent=2) + "\n"
    )
    write_rows(rows, out / "buildings.csv")
    (out / REPORT_NAME).write_text(_report(baseline, result, by_type))
    return result


def load_fixtures(
    fixtures: Path, params: PipelineParams | None = None
) -> tuple[list[FootprintOutcome], list[ReferenceBuilding]]:
    """Reconstruct every fixture footprint and read every fixture reference building."""
    index = json.loads((fixtures / "index.json").read_text())
    footprints: list[FootprintOutcome] = []
    buildings: dict[str, ReferenceBuilding] = {}
    for entry in index["footprints"]:
        fid = entry["footprint_id"]
        feature = json.loads((fixtures / "footprints" / f"{fid}.geojson").read_text())
        feature = feature["features"][0]
        polygon = shapely.from_geojson(json.dumps(feature["geometry"]))
        result = reconstruct_footprint(
            read_points(fixtures / "points" / f"{fid}.laz"), polygon, params
        )
        footprints.append(
            FootprintOutcome(
                footprint_id=fid,
                polygon=polygon,
                status=result.status.value,
                faces=result.faces,
                roof_points=result.roof_points,
                density=result.density,
                lidar_derived=bool(feature["properties"].get("lidar_derived", False)),
                reason=result.reason,
                warnings=tuple(result.warnings),
            )
        )
        reference = json.loads((fixtures / "reference" / f"{fid}.json").read_text())
        for b in reference["buildings"]:
            roofs = [
                RoofPolygon(b["gml_id"], [(p[0], p[1], p[2]) for p in ring])
                for ring in b["roof_polygons"]
            ]
            buildings[b["gml_id"]] = ReferenceBuilding(b["gml_id"], roofs)
    return footprints, list(buildings.values())


def write_rows(rows: list[BuildingResult], path: Path) -> None:
    """Write the per-building results table as CSV, one row per building."""
    names = [f.name for f in dataclasses.fields(BuildingResult)]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=names)
        writer.writeheader()
        for row in rows:
            record = dataclasses.asdict(row)
            record["warnings"] = ";".join(row.warnings)
            writer.writerow(record)


def write_pending_report(out: Path, reason: str) -> Path:
    """Write the report for a smoke run that measured nothing, and say why."""
    out.mkdir(parents=True, exist_ok=True)
    path = out / REPORT_NAME
    path.write_text(f"### eval-smoke\n\n:x: **Nothing was measured:** {reason}\n")
    return path


def _report(baseline: dict[str, Any], result: SmokeResult, by_type: dict[str, Any]) -> str:
    expected = baseline.get("metrics", {}).get("overall", {})
    tolerances = baseline.get("tolerances", {})
    if not result.gating:
        verdict = (
            ":construction: The baseline is all `null`, so this guard **reports but does "
            "not gate**. It gates once `baseline_metrics.json` has real numbers."
        )
    elif result.regressions:
        verdict = ":x: **Regression.**\n\n" + "\n".join(f"- {m}" for m in result.regressions)
    else:
        verdict = ":white_check_mark: No headline metric regressed beyond tolerance."

    lines = [
        "### eval-smoke",
        "",
        verdict,
        "",
        "| metric | baseline | this run | Δ | tolerance |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, label, fmt in REPORTED:
        before, after = expected.get(name), result.overall.get(name)
        delta = "" if before is None or after is None else _fmt(fmt, after - before, signed=True)
        tol = tolerances.get(name)
        lines.append(
            f"| {label} | {_fmt(fmt, before)} | {_fmt(fmt, after)} | {delta} | "
            f"{'' if tol is None else tol} |"
        )
    o = result.overall
    lines += [
        "",
        f"{o['n_buildings']} reference buildings, {o['n_assigned']} assigned, "
        f"{o['n_grouped']} grouped; {o['n_no_reference']} footprints with no reference "
        "building.",
        "",
        "Statuses: " + ", ".join(f"`{k}` {v}" for k, v in o["status_counts"].items()),
        "",
        "| roof type | buildings | RMSE (m) | coverage | reconstructed |",
        "|---|---:|---:|---:|---:|",
    ]
    for roof_type, summary in by_type.items():
        lines.append(
            f"| {roof_type} | {summary['n_buildings']} | "
            f"{_fmt('{:.3f}', summary['vertical_rmse_m'])} | "
            f"{_fmt('{:.3f}', summary['coverage'])} | {summary['buildings_reconstructed']} |"
        )
    return "\n".join(lines) + "\n"


def _fmt(fmt: str, value: Any, signed: bool = False) -> str:
    if value is None:
        return "—"
    if signed:
        fmt = fmt.replace("{:", "{:+")
    return fmt.format(value)
