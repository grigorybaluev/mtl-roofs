/**
 * Viewer entry point.
 *
 * The renderer choice (Three.js vs CesiumJS) is recorded in
 * docs/adr/0003-viewer-renderer.md. Tileset loading is deliberately not wired up
 * yet: data products are built locally and attached to GitHub Releases, and the
 * Pages workflow downloads them at deploy time.
 */
import { renderMetrics, type BuildingMetrics } from "./metrics";

const layerState = new Map<string, boolean>();

function bindLayerToggles(): void {
  const inputs = document.querySelectorAll<HTMLInputElement>("#layers input[data-layer]");
  for (const input of inputs) {
    const layer = input.dataset["layer"];
    if (layer === undefined) {
      continue;
    }
    layerState.set(layer, input.checked);
    input.addEventListener("change", () => {
      layerState.set(layer, input.checked);
    });
  }
}

/** Whether a named layer is currently visible. Exported for tests. */
export function isLayerVisible(layer: string): boolean {
  return layerState.get(layer) ?? false;
}

function main(): void {
  bindLayerToggles();
  const panel = document.querySelector<HTMLElement>("#metrics");
  if (panel !== null) {
    const placeholder: BuildingMetrics | null = null;
    renderMetrics(panel, placeholder);
  }
}

main();
