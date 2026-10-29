import type { BuildingRow, Footprint, Reference, SceneIndex, Summary } from "./scene";

/** Format a metric for the panel, making a missing value explicit. */
export function formatMetric(value: number | null, unit: string, digits = 2): string {
  if (value === null || Number.isNaN(value)) {
    return "—";
  }
  return `${value.toFixed(digits)}${unit}`;
}

function pct(value: number | null, digits = 1): string {
  return formatMetric(value === null ? null : value * 100, " %", digits);
}

function esc(s: string): string {
  return s.replace(/[&<>"]/g, (c) => `&#${c.charCodeAt(0)};`);
}

function table(rows: [string, string][]): string {
  return `<table>${rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("")}</table>`;
}

/** What a reconstruction status means, for someone who has not read the docs. */
const STATUS_TEXT: Record<string, string> = {
  ok: "reconstructed",
  "no-planes": "not reconstructed: no roof plane found in the points",
  "too-few-points": "not reconstructed: too few LiDAR points on the roof",
  "not-watertight": "not reconstructed: the solid would not close",
};

/** The area's headline numbers, as `mtl-roofs evaluate` computed them. */
export function renderSummary(target: HTMLElement, index: SceneIndex): void {
  const s: Summary = index.summary;
  const ok = s.footprintStatus["ok"] ?? 0;
  target.innerHTML =
    table([
      ["Vertical RMSE, pooled", formatMetric(s.rmse, " m")],
      ["Median per building", formatMetric(s.medianBuildingRmse, " m")],
      ["Coverage", pct(s.coverage)],
      ["Orientation, median", formatMetric(s.orientationMedian, "°", 1)],
      ["Orientation, p90", formatMetric(s.orientationP90, "°", 1)],
      ["City buildings rebuilt", `${s.buildingsReconstructed.toLocaleString()} / ${s.buildings.toLocaleString()}`],
      ["Footprints rebuilt", `${ok.toLocaleString()} / ${s.footprints.toLocaleString()}`],
    ]) +
    `<p class="note">Scored against the city's independent 2016 LOD2 model on a 0.5 m
     grid. Orientation and face counts are biased by its triangulated roofs
     (<a href="https://github.com/grigorybaluev/mtl-roofs/issues/96">#96</a>).</p>
     <p class="note">Area ${esc(index.area)} · run ${esc(index.runId)}</p>`;
}

function buildingRows(b: BuildingRow): [string, string][] {
  return [
    ["City building", esc(b.id)],
    ["Roof type", b.roofType ?? "—"],
    ["Vertical RMSE", formatMetric(b.rmse, " m")],
    ["Coverage", pct(b.coverage)],
    ["Orientation error", formatMetric(b.orientation, "°", 1)],
    ["Faces, ours / city", `${b.predFaces ?? "—"} / ${b.refFaces ?? "—"}`],
    ["LiDAR − city height", formatMetric(b.temporalOffset, " m")],
  ];
}

/** One footprint's reconstruction and the city buildings scored against it. */
export function renderFootprint(
  target: HTMLElement,
  fp: Footprint | null,
  ref: Reference | null = null,
): void {
  if (fp === null) {
    target.classList.add("empty");
    target.textContent =
      ref === null
        ? "Click a building to see its metrics."
        : `City building ${ref.id} has no footprint to score against.`;
    return;
  }
  target.classList.remove("empty");
  const ok = fp.status === "ok";
  let html = `<h2>${esc(fp.id)}</h2>
    <p class="status ${ok ? "ok" : "failed"}">${ok ? "✓" : "✕"} ${esc(STATUS_TEXT[fp.status] ?? fp.status)}</p>`;
  const rows: [string, string][] = [
    ["Roof points", fp.points === null ? "—" : fp.points.toLocaleString()],
    ["Density", formatMetric(fp.density, " pts/m²", 1)],
  ];
  if (fp.buildings.length > 1) {
    rows.push([`RMSE, all ${fp.buildings.length} buildings`, formatMetric(fp.rmse, " m")]);
  }
  html += table(rows);
  if (fp.buildings.length === 0) {
    html += `<p class="note">The city model has no building here, so this footprint is not scored.</p>`;
  }
  for (const b of fp.buildings) {
    html += table(buildingRows(b));
  }
  target.innerHTML = html;
}
