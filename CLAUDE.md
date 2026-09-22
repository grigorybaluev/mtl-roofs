# CLAUDE.md

Guidance for Claude Code sessions in this repository.

## What this is

Reconstructs LOD2 building roofs for Montréal from open aerial LiDAR and building
footprints, and scores every building against the city's own 2016 LOD2 CityGML model.
**v0** — one neighbourhood (CDN–NDG tile 03, 1,109 buildings), classical pipeline only,
full evaluation report, viewer deployed.
**v1** — ML roof typing with ablations against the classical pipeline on identical
metrics, scaled to a full borough.
The point of the project is the honest measurement, not the picture.

## Environment — read before installing anything

- Machine: **macOS 12 Monterey, Intel (x86_64)**. Homebrew has no bottles for this OS
  and compiles from source.
- **Never use Homebrew for GDAL, PDAL, PROJ, GEOS or PostGIS.** Use **pixi** with
  conda-forge, which ships prebuilt `osx-64` binaries.
- `pixi.toml` declares `osx-64` (with a **macOS 12.0 floor**) and `linux-64`, so CI and
  this machine share one lockfile. Do not remove the floor: without it the solver picks
  `osx-64` builds requiring macOS ≥ 13 that cannot run here.
- Node and pnpm come **from pixi**, in the `viewer` environment. The Homebrew node on
  this machine is broken (missing `libicui18n.73.dylib`); always run viewer commands
  through `pixi run -e viewer …` so pixi's node is first on `PATH`.
- **PostGIS runs in Docker only.** No local PostgreSQL. The container binds host port
  **5433**, not 5432 — 5432 is taken by an unrelated project on this machine.
- `py3dtiles` is deliberately absent: it is not on conda-forge and a transitive source
  build fails on `osx-64`. See `docs/adr/0002-3d-tiles-writer.md`. Do not add it back
  without resolving that ADR.

## Commands

```bash
pixi install                      # default env
pixi install -e viewer            # node + pnpm

pixi run lint                     # ruff check
pixi run format                   # ruff format + --fix
pixi run typecheck                # mypy --strict src
pixi run test                     # unit tests + coverage
pixi run test-integration         # needs db-up
pixi run eval-smoke               # metrics on tests/fixtures  (the regression guard)
pixi run db-up / db-down          # PostGIS container, waits for healthcheck

pixi run -e viewer viewer-dev     # vite dev server
pixi run -e viewer viewer-build   # tsc --noEmit && vite build
pixi run -e viewer viewer-typecheck
```

CLI:

```bash
mtl-roofs version
mtl-roofs data areas              # study areas in configs/areas/
mtl-roofs data plan  -a <area>    # what would be downloaded, no download
mtl-roofs data fetch -a <area>    # stream + verify only the needed tiles
mtl-roofs data verify -a <area>   # re-check local artefacts offline, no network
mtl-roofs data checksums -a <area>  # print the YAML to paste into data/manifest.yaml
mtl-roofs reconstruct -a <area>
mtl-roofs evaluate    -a <area>
mtl-roofs export      -a <area>
mtl-roofs report      -a <area>
```

## Workflow contract

1. Pick an issue from the project board, column **Ready**.
2. Branch `<type>/<issue-number>-<slug>`, e.g. `feat/14-ransac-plane-detection`.
3. Conventional commits (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`, `perf:`,
   `test:`, `ci:`, `data:`, `experiment:`).
4. Open a PR with `Closes #<n>` and fill in the template — including the evaluation
   impact table if geometry, evaluation or ML code changed.
5. The PR must pass **ci** and **eval-smoke** (and **viewer** if `viewer/**` changed).
6. **Squash merge.** The PR title becomes the commit message, so it must be a valid
   conventional commit — `pr-title.yml` enforces this.
7. For `type:experiment` issues, fill in the **Result** section before closing, even
   when the hypothesis was wrong. Especially then.

## Data rules

- **Never commit data.** `data/` is git-ignored. Raw or derived LiDAR, meshes, rasters
  and tiles do not go into git, ever.
- The only exception is `tests/fixtures/`: a handful of buildings, **each file under
  2 MB**. A pre-commit hook enforces the limit.
- Any new source goes in `data/manifest.yaml` (URL, size, SHA-256) **and**
  `docs/data-sources.md` (licence, CRS, coverage, date retrieved, pitfalls) in the
  same PR.
- **Pinning a checksum is a human act.** `data checksums` prints the YAML; you paste
  it after satisfying yourself the download is good. Nothing writes to the manifest
  automatically, because a tool that pinned whatever it just downloaded would certify
  a corrupt file exactly as confidently as a good one.
- Checksums are taken over the **artefact that lands in `data/raw/`**, never the
  enclosing archive — the LiDAR archives are 20–28 GB and are never fetched whole.
- Verify facts about external data against the source, never from a description.
  The project brief asserted EPSG:32188; the data says **2950**.

## Geometry conventions

| Concept | Convention |
|---|---|
| Horizontal CRS | **EPSG:2950** — NAD83(CSRS) / MTM zone 8. Not 32188. |
| Units | metres, everywhere, no exceptions |
| Vertical datum | **CGVD28**. No vertical transform is applied to either dataset. |
| LiDAR tiles | 1000 m squares, named `<easting_km>-<northing_km>`, member `<tile>_2015.las` |
| Polygon winding | exterior rings **counter-clockwise** seen from outside; holes clockwise |
| Face normals | outward-facing; roof plane normals stored canonically with `nz ≥ 0` |
| Building id | our own stable id, carried end to end: footprint → reconstruction → metrics → tileset batch table. The footprint layer has **no id of its own** and CityGML `gml:id` is a different namespace; the mapping between them is produced by spatial matching and stored, never recomputed ad hoc. |

## Design decisions

When a choice has a real alternative someone could reasonably prefer, write an ADR in
`docs/adr/` using `template.md`, and label the issue **`needs-decision`** until the
maintainer confirms. Do not implement past an unresolved ADR. Accepted ADRs are
immutable — supersede, never edit.

Currently open: `0002` (3D Tiles writer), `0003` (Three.js vs CesiumJS).

## Evaluation is sacred

- Metric definitions live in `docs/evaluation.md` and are written **before** the
  implementation.
- **Never change a metric definition or `tests/fixtures/baseline_metrics.json` in the
  same PR as an algorithm change.** `eval-smoke.yml` fails the build if you do, because
  the two effects cannot be separated by a reviewer.
- A baseline update is its own PR, with a written justification for every number that
  moved.
- Report RMSE **and** coverage together, always. A reconstruction covering 10% of a
  roof can score a beautiful RMSE.
- Failures belong in the results table with a status, not omitted from it.
