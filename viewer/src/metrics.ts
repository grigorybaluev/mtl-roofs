/** Per-building metrics as served alongside the tileset. */
export interface BuildingMetrics {
  buildingId: string;
  roofType: "flat" | "mixed" | "pitched";
  rmse: number | null;
  coverage: number | null;
  orientationError: number | null;
  predictedFaces: number | null;
  referenceFaces: number | null;
  status: string;
}

/** Format a metric for the panel, making a missing value explicit. */
export function formatMetric(value: number | null, unit: string, digits = 2): string {
  if (value === null || Number.isNaN(value)) {
    return "—";
  }
  return `${value.toFixed(digits)}${unit}`;
}

/** Render a metrics table, or an explanation of why there is none. */
export function renderMetrics(target: HTMLElement, metrics: BuildingMetrics | null): void {
  if (metrics === null) {
    target.classList.add("empty");
    target.textContent = "Select a building to see its metrics.";
    return;
  }
  target.classList.remove("empty");
  const rows: [string, string][] = [
    ["Building", metrics.buildingId],
    ["Roof type", metrics.roofType],
    ["Vertical RMSE", formatMetric(metrics.rmse, " m")],
    ["Coverage", formatMetric(metrics.coverage === null ? null : metrics.coverage * 100, " %", 1)],
    ["Orientation error", formatMetric(metrics.orientationError, "°", 1)],
    ["Faces (pred / ref)", `${metrics.predictedFaces ?? "—"} / ${metrics.referenceFaces ?? "—"}`],
    ["Status", metrics.status],
  ];
  target.innerHTML = `<table>${rows
    .map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`)
    .join("")}</table>`;
}
