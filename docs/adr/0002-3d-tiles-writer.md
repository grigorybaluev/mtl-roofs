# 0002. 3D Tiles writer

- **Status:** proposed — blocks the "3D Tiles export" issue
- **Date:** 2026-09-22

## Context

The viewer needs a streamable tileset of ~1,100 reconstructed buildings for v0, and a
full borough at v1.

`py3dtiles` was the obvious candidate and is named in the project brief. It does not
work here, and this was verified rather than assumed:

- it is **not packaged on conda-forge**, so it must come from PyPI into a conda
  environment;
- on `osx-64` a transitive dependency builds from source and fails against modern
  setuptools (`TypeError: Popen.__init__() got an unexpected keyword argument
  'dry_run'`, from a vendored distutils `spawn` call);
- forcing it to resolve additionally required pinning `numpy < 2.3`, holding the
  whole numerical stack back for one writer.

It is therefore absent from `pixi.toml`. The export module is a stub, and no
workaround was quietly committed.

## Options

### A. `py3dtiles`, pinned, with numpy held back
**Pros:** purpose-built; handles b3dm/pnts.
**Cons:** does not install on the development machine at all; would pin numpy for the
entire project; CI and local would diverge.

### B. Write the tileset directly
`tileset.json` plus glTF/GLB content written from our own meshes. LOD2 roof solids are
small, flat-shaded and untextured; the b3dm container is a thin wrapper over glB.
**Pros:** no dependency; full control over batch table (building id, metrics) which
the viewer needs for picking; works everywhere.
**Cons:** we own the spec compliance; more code to test.

### C. glTF only, no 3D Tiles
**Pros:** simplest.
**Cons:** no spatial subdivision or LOD, so it does not scale to a borough at v1 —
which is a stated v1 goal.

### D. Export via CesiumJS tooling / `cesium-native`
**Pros:** reference implementation.
**Cons:** heavyweight toolchain; poor fit for a pixi/conda project.

## Decision

**Not yet decided.** Leaning to **B**, because the geometry is simple, the batch table
is exactly what the metrics panel needs, and it removes a dependency that has already
proven unbuildable on the target machine. Decide together with
[0003](0003-viewer-renderer.md) — the renderer determines what the tileset must satisfy.

## Consequences

`export/tiles3d.py` stays a stub and the "3D Tiles export" issue keeps the
`needs-decision` label until this ADR is accepted. If B is chosen, spec conformance
needs its own tests, since there is no library to trust.
