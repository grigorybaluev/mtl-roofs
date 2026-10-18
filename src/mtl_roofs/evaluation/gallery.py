"""The failure gallery: the worst buildings by RMSE, each with a diagnosis (#27).

A report that shows only successes is not finished (``docs/evaluation.md``). The
gallery renders the ``n`` scored buildings with the highest RMSE, each in four
panels, the input LiDAR, the reconstruction, the reference and the height
difference, with its status and a one-line diagnosis. It also lists every building
that was not scored at all, with its reason, and the status distribution over the
whole area.

Diagnoses are rules over the building's own numbers, checked in order. They say what
the error looks like, not why it happened; that stays a human judgement.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from mtl_roofs.evaluation.evaluate import RECONSTRUCTED, FootprintOutcome
from mtl_roofs.evaluation.grid import building_cells, predicted_surface, reference_surface
from mtl_roofs.evaluation.report import BuildingResult
from mtl_roofs.io.reference import ReferenceBuilding, roof_outline

FloatArray = npt.NDArray[np.float64]

#: A cell further off than this counts as badly reconstructed in a diagnosis.
BAD_CELL_M = 1.0


@dataclass(frozen=True, slots=True)
class Difference:
    """Height difference of one building on the evaluation grid."""

    x: FloatArray
    y: FloatArray
    dz: FloatArray  # predicted minus reference; NaN where either is undefined

    @property
    def bias(self) -> float | None:
        """Median signed difference, ``None`` with no common cell."""
        valid = self.dz[np.isfinite(self.dz)]
        return float(np.median(valid)) if len(valid) else None


def difference(
    building: ReferenceBuilding, footprint: FootprintOutcome, outline: Any
) -> Difference:
    """Predicted minus reference height on a building's evaluation cells."""
    x, y = building_cells(outline)
    z_ref = reference_surface([r.points for r in building.roofs], x, y)
    z_pred = predicted_surface(footprint.faces, x, y)
    return Difference(x, y, z_pred - z_ref)


def diagnose(row: BuildingResult, diff: Difference | None = None) -> str:
    """One line on what a building's error looks like, from its own numbers."""
    if row.status == "suspect-temporal" and row.temporal_offset_m is not None:
        return (
            f"The LiDAR is {row.temporal_offset_m:+.1f} m from the reference: probably "
            "changed between 2015 and 2016."
        )
    if row.status not in RECONSTRUCTED:
        detail = f": {row.reason}" if row.reason else ""
        return f"Not reconstructed (`{row.status}`){detail}."
    if row.coverage is not None and row.coverage < 0.9:
        return f"The reconstruction covers only {row.coverage:.0%} of the reference roof."
    if row.share is not None and row.share < 0.9:
        return (
            f"Only {row.share:.0%} of the reference roof lies in the footprint; the "
            "rest is scored against nothing."
        )
    bias = diff.bias if diff is not None else None
    if bias is not None and row.rmse_m and abs(bias) > 0.5 and abs(bias) > 0.7 * row.rmse_m:
        side = "above" if bias > 0 else "below"
        return f"The whole roof sits {abs(bias):.1f} m {side} the reference."
    if row.orientation_median_deg is not None and row.orientation_median_deg > 5.0:
        return f"Planes tilted: median orientation error {row.orientation_median_deg:.1f}°."
    if diff is None:
        return "No single pattern; see the difference panel."
    valid = diff.dz[np.isfinite(diff.dz)]
    bad = np.abs(valid) > BAD_CELL_M
    if not bad.any():
        return f"Spread error: no cell is off by more than {BAD_CELL_M:.0f} m."
    side = "too high" if np.median(valid[bad]) > 0 else "too low"
    rest = float(np.sqrt(np.mean(valid[~bad] ** 2))) if (~bad).any() else 0.0
    share = float(bad.mean())
    kind = "One part of the roof is off" if share >= 0.05 else "Small, isolated errors"
    return (
        f"{kind}: {share:.0%} of cells by more than {BAD_CELL_M:.0f} m, mostly {side}; "
        f"the rest within {rest:.2f} m RMSE."
    )


def write_gallery(
    rows: Sequence[BuildingResult],
    footprints: Mapping[str, FootprintOutcome],
    buildings: Mapping[str, ReferenceBuilding],
    out: Path,
    n: int = 20,
    title: str = "Failure gallery",
) -> Path:
    """Render the ``n`` worst scored buildings and write ``gallery.md`` beside them.

    Args:
        rows: Every row of the evaluation, failures included.
        footprints: Footprints by id, with faces and roof points.
        buildings: Reference buildings by ``gml:id``.
        out: Directory for ``gallery.md`` and the figures.
        n: How many buildings to render.
        title: Heading of the page.

    Returns:
        The path of ``gallery.md``.
    """
    out.mkdir(parents=True, exist_ok=True)
    scored = [r for r in rows if r.rmse_m is not None and r.footprint_id is not None]
    worst = sorted(scored, key=lambda r: (-(r.rmse_m or 0.0), r.building_id))[:n]
    unscored = [
        r
        for r in rows
        if r.status not in RECONSTRUCTED and r.status not in ("no-reference", "grouped-reference")
    ]
    statuses = Counter(r.status for r in rows)

    lines = [
        f"# {title}",
        "",
        f"The {len(worst)} buildings with the highest vertical RMSE, of {len(scored)} "
        "scored. Each figure shows, left to right, the footprint's roof points, the "
        "reconstruction, the reference roof, and the reconstruction minus the reference "
        "on the 0.5 m evaluation grid (red: too high, blue: too low).",
        "",
        "## Status of every building and footprint",
        "",
        "| status | rows |",
        "|---|---:|",
        *(f"| `{s}` | {c} |" for s, c in sorted(statuses.items())),
        "",
        "## The worst buildings",
    ]
    for rank, row in enumerate(worst, start=1):
        assert row.footprint_id is not None
        footprint = footprints[row.footprint_id]
        building = buildings[row.building_id]
        outline = roof_outline(building)
        diff = difference(building, footprint, outline)
        image = f"{rank:02d}-{row.building_id}.png"
        _render(out / image, row, footprint, building, outline, diff)
        lines += [
            "",
            f"### {rank}. `{row.building_id}` on `{row.footprint_id}`",
            "",
            f"`{row.status}` · {row.roof_type} · RMSE {row.rmse_m:.2f} m · coverage "
            f"{row.coverage or 0:.0%} · {row.outline_area_m2:.0f} m² · "
            f"{row.point_density or 0:.1f} pts/m²",
            "",
            f"**Diagnosis.** {diagnose(row, diff)}",
            "",
            f"![{row.building_id}]({image})",
        ]
    lines += ["", "## Buildings not scored", ""]
    if unscored:
        lines += ["| building | footprint | status | diagnosis |", "|---|---|---|---|"]
        lines += [
            f"| `{r.building_id}` | `{r.footprint_id}` | `{r.status}` | {diagnose(r)} |"
            for r in sorted(unscored, key=lambda r: (r.status, r.building_id))
        ]
    else:
        lines.append("None: every assigned building was reconstructed.")
    path = out / "gallery.md"
    path.write_text("\n".join(lines) + "\n")
    return path


def _render(
    path: Path,
    row: BuildingResult,
    footprint: FootprintOutcome,
    building: ReferenceBuilding,
    outline: Any,
    diff: Difference,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection

    pts = footprint.roof_points
    z_all = [z for r in building.roofs for _, _, z in r.points]
    if len(pts):
        z_all += [float(np.percentile(pts[:, 2], 2)), float(np.percentile(pts[:, 2], 98))]
    vmin, vmax = min(z_all), max(z_all)
    x0, y0 = footprint.polygon.bounds[:2]

    fig, axes = plt.subplots(1, 4, figsize=(16, 4.4))
    ax_pts, ax_pred, ax_ref, ax_diff = axes
    if len(pts):
        ax_pts.scatter(
            pts[:, 0] - x0, pts[:, 1] - y0, c=pts[:, 2], s=2, cmap="viridis", vmin=vmin, vmax=vmax
        )
    ax_pts.set_title(f"LiDAR roof points ({len(pts)})")

    pred_polys, pred_z = [], []
    for face in footprint.faces:
        xy = np.asarray(face.polygon.exterior.coords) - [x0, y0]
        c = face.polygon.centroid
        nx, ny, nz = face.plane.normal
        pred_polys.append(xy)
        pred_z.append((face.plane.offset - nx * c.x - ny * c.y) / nz)
    ax_pred.add_collection(
        PolyCollection(
            pred_polys, array=np.asarray(pred_z), cmap="viridis", edgecolors="k", linewidths=0.6
        )
    ).set_clim(vmin, vmax)
    ax_pred.set_title(f"reconstruction ({len(footprint.faces)} faces)")

    ref_polys = [np.asarray([p[:2] for p in r.points]) - [x0, y0] for r in building.roofs]
    ref_z = [float(np.mean([p[2] for p in r.points])) for r in building.roofs]
    ax_ref.add_collection(
        PolyCollection(
            ref_polys, array=np.asarray(ref_z), cmap="viridis", edgecolors="k", linewidths=0.3
        )
    ).set_clim(vmin, vmax)
    ax_ref.set_title(f"reference ({len(building.roofs)} faces)")

    shown = ax_diff.scatter(
        diff.x - x0, diff.y - y0, c=diff.dz, s=9, marker="s", cmap="RdBu_r", vmin=-2, vmax=2
    )
    fig.colorbar(shown, ax=ax_diff, label="predicted - reference (m)", shrink=0.8)
    ax_diff.set_title(f"difference, RMSE {row.rmse_m:.2f} m")

    for ax in axes:
        for geom in getattr(outline, "geoms", [outline]):
            ox, oy = geom.exterior.xy
            ax.plot(np.asarray(ox) - x0, np.asarray(oy) - y0, color="tab:orange", lw=1)
        fx, fy = footprint.polygon.exterior.xy
        ax.plot(np.asarray(fx) - x0, np.asarray(fy) - y0, color="k", lw=0.8, ls="--")
        ax.set_aspect("equal")
        ax.autoscale_view()
        ax.tick_params(labelsize=7)
    fig.suptitle(f"{row.building_id} — {row.status}", fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=80)
    plt.close(fig)
