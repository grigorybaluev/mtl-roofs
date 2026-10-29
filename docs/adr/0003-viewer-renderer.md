# 0003. Viewer renderer: Three.js or CesiumJS

- **Status:** accepted (A, for v0) · proposed 2026-09-22
- **Date:** 2026-10-29

## Context

The viewer must show, per building: input LiDAR points, our reconstruction, the
reference model, and the error between them; let the user pick a building and read its
metrics. It is deployed as a static site on GitHub Pages, and data products are
attached to GitHub Releases rather than built in CI.

The study area is ~1.5 × 1.1 km with 1,109 buildings — small. A full borough at v1 is
tens of thousands.

## Options

### A. Three.js
**Pros:** small bundle; total control over materials, which matters because error
colouring is a custom shaded attribute rather than a basemap overlay; trivial to
render four co-located layers with independent visibility; no globe machinery for a
1 km² scene; a scaffold already exists in `viewer/`.
**Cons:** no 3D Tiles support out of the box (`3d-tiles-renderer` is a third-party
add-on); no georeferencing, terrain or basemap for free; we implement picking and
camera controls ourselves.

### B. CesiumJS
**Pros:** native 3D Tiles streaming and LOD; real geographic context with terrain and
imagery; scales to a borough without extra work; `Cesium3DTileFeature` picking and
per-feature styling are built in and map cleanly onto a batch table.
**Cons:** much larger bundle; needs projection from EPSG:2950 to ECEF/WGS84 for
display, adding a transform to verify; ion account nudges for assets; styling error
gradients is more constrained than a custom shader.

## Decision

**A, Three.js, for v0.** Confirmed by the maintainer on 2026-10-29.

v0's point is the honest evaluation, and the viewer exists to show it: per-building error,
the failures, and the reference next to the reconstruction. The v0 area is small enough to
ship whole (about 15 MB for 1,556 footprints, 39k reference triangles and a 1.5 M-point
LiDAR sample), so A's missing tiling costs nothing yet. B's advantages only pay off at
borough scale, which is v1.

v0 therefore does not use 3D Tiles at all: `mtl-roofs export-scene` writes a minimal
static scene (`scene.json` + `scene.bin`, documented in `src/mtl_roofs/export/scene.py`)
that the viewer loads in one request. This leaves [0002](0002-3d-tiles-writer.md) open
rather than deciding it by accident; it becomes the v1 question, together with revisiting
this ADR if a borough does not fit in one scene.

## Consequences

- The viewer is Three.js with OrbitControls, framework-free TypeScript, as scaffolded.
- Hosting is a release asset: the scene is zipped as `viewer-data-<area>.zip`, attached to
  a GitHub release, and copied next to the viewer by `pages.yml`. No data enters git.
- At v1 scale this ADR is to be superseded, not edited, if one scene no longer fits.
