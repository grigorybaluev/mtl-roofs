# 0003. Viewer renderer: Three.js or CesiumJS

- **Status:** proposed — blocks the "Viewer" and "3D Tiles export" issues
- **Date:** 2026-09-22

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

**Not yet decided.** The trade-off turns on v1: if borough scale is a firm goal, B's
native tiling pays for itself and settles [0002](0002-3d-tiles-writer.md) toward a
conformant tileset. If v0's honest evaluation is the point and v1 scale is aspirational,
A is lighter and better at the error visualisation that is the whole reason for the
viewer.

Note the interaction: choosing A makes B-in-0002 ("write the tileset ourselves") more
attractive, since we would control both ends and could keep the format minimal.

## Consequences

`viewer/` currently has a framework-free TypeScript scaffold — `three` is declared but
the scene is not built — so neither option is foreclosed. The "ADR: Three.js vs
CesiumJS" issue carries `needs-decision`, and the viewer issue stays blocked until
this is resolved.
