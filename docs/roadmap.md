# Roadmap

Tracked on the [project board](https://github.com/users/grigorybaluev/projects) and in
[milestones](https://github.com/grigorybaluev/mtl-roofs/milestones). This page is the
narrative; the board is the truth.

## v0 — "one neighbourhood, honest numbers"

**Goal:** one neighbourhood reconstructed by the classical pipeline, evaluated against
the city's LOD2 model with numbers nobody has to take on trust, and a viewer anyone can
open.

**Study area:** CDN–NDG tile 03 — 1,109 buildings, measured at 45.7% pitched / 20.1%
mixed / 34.2% flat, covered by four 1 km LiDAR tiles. Chosen over Outremont and the
Plateau on measured roof-type variety; see [`data-sources.md`](data-sources.md).

**Done when:**

- `mtl-roofs data fetch -a cdn-ndg-03` retrieves and verifies every source
- every building produces a row — including a status for the ones that fail
- the README results table has real numbers, and a GIF
- the viewer is live on GitHub Pages
- `v0.1.0` is tagged

| Epic | What it delivers |
|---|---|
| Data foundation | verified sources, checksummed fetch, PostGIS load, real test fixtures |
| Classical reconstruction | clipping, plane detection, topology solver, LOD2 extrusion, batch run |
| Evaluation | metric definitions first, then matching, RMSE, orientation, breakdowns, failure gallery |
| Serving & viewer | renderer ADR, 3D Tiles export, viewer, Pages deploy |
| v0 release | write-up of method **and limitations**, tag |

**Open decisions blocking work:** [ADR 0002](adr/0002-3d-tiles-writer.md) (3D Tiles
writer) and [ADR 0003](adr/0003-viewer-renderer.md) (Three.js vs CesiumJS). Both are
labelled `needs-decision`.

## v1 — "ML + borough scale"

**Goal:** show that an ML component is worth its complexity — or honestly show that it
is not — and scale from one tile to a borough.

| Epic | What it delivers |
|---|---|
| ML roof typing / segmentation | labels from the reference model, spatially blocked splits, gradient-boosting baseline, small CPU point-cloud net, ablation against the classical pipeline on identical metrics |
| Borough scale | chunked resumable processing, profiling and memory limits, tile hosting ADR |
| v1 release | comparison write-up, tag |

The ablation is the deliverable, not the model. Both pipelines are scored with the same
frozen metrics on the same split, and a negative result gets published.

## Deliberately out of scope

- LOD3 (façade detail) — the reference model cannot score it: its walls are synthetic
- textures and photogrammetric appearance
- city-wide processing — 684 tiles and 119 GB is an infrastructure project, not a
  geometry one
- real-time or incremental updates
