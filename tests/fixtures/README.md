# Test fixtures

Twenty real footprints carved out of the v0 study area, holding 35 reference
buildings — **not synthetic**. They are
what makes `eval-smoke` a genuine regression guard rather than a self-fulfilling one.

| | |
|---|---|
| LiDAR | tile `292-5035` of *LiDAR aérien 2015* |
| Reference | tile `CDNNDG03` of *Bâtiments 3D 2016 (Maquette LOD2)* |
| Footprints | `CARTO-BAT-TOIT` of *Bâtiments 2D 2016* |
| CRS | EPSG:2950 (NAD83(CSRS) / MTM zone 8), metres, CGVD28 |
| Licence | CC BY 4.0, Ville de Montréal — see `docs/data-sources.md` |

## Layout

```
footprints/<footprint_id>.geojson  the CARTO-BAT-TOIT polygon and its provenance
points/<footprint_id>.laz          LiDAR clipped to the footprint + 2 m of context
reference/<footprint_id>.json      every reference building assigned to the footprint
index.json                         per-footprint and per-building metadata
baseline_metrics.json              the eval-smoke baseline and its tolerances
```

Fixtures are cut **per footprint**, not per reference building
([ADR 0005](../../docs/adr/0005-reconstruct-per-footprint-score-per-building.md)). A
footprint is often a semi-detached pair or a row, and a fixture cut to one reference
building would give the reconstruction half a roof. `footprint_id` is `fp-<E>-<N>`,
the footprint centroid in decimetres; `building_id` is the reference `gml:id`.

Regenerate with `pixi run python scripts/build_fixtures.py`, after
`mtl-roofs data fetch -a cdn-ndg-03`.

## Composition

Built around 20 seed buildings stratified so that every code path is exercised: 6
pitched, 5 mixed, 5 flat, and 4 *complex* (≥ 40 reference roof faces, up to 73).
Their footprints add 15 neighbours, for 35 reference buildings: 10 pitched, 7
mixed, 13 flat and 5 complex. One footprint is a row of 901 m². Flat roofs include two single-face
buildings — the degenerate case where plane detection must not over-segment — and the
complex ones are where the topology solver is expected to struggle.

## Rules

- Every file stays **under 2 MB**; the whole set is ~0.95 MB. A pre-commit hook enforces
  the limit, and `tests/unit/test_fixtures.py` asserts it too.
- These are the only binaries in the repository. Nothing else from `data/` is ever
  committed.
- Regenerating them changes the baseline, so it is **its own pull request** with a
  justification. See `CLAUDE.md`.
