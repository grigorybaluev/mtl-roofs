# Test fixtures

Twenty real buildings carved out of the v0 study area — **not synthetic**. They are
what makes `eval-smoke` a genuine regression guard rather than a self-fulfilling one.

| | |
|---|---|
| LiDAR | tile `292-5035` of *LiDAR aérien 2015* |
| Reference | tile `CDNNDG03` of *Bâtiments 3D 2016 (Maquette LOD2)* |
| CRS | EPSG:2950 (NAD83(CSRS) / MTM zone 8), metres, CGVD28 |
| Licence | CC BY 4.0, Ville de Montréal — see `docs/data-sources.md` |

## Layout

```
points/<building_id>.laz      LiDAR clipped to the footprint + 2 m of context
reference/<building_id>.json  the reference roof polygons for that building
index.json                    per-building metadata (roof type, faces, density)
baseline_metrics.json         the eval-smoke baseline and its tolerances
```

## Composition

Stratified so that every code path is exercised: 6 pitched, 5 mixed, 5 flat, and 4
*complex* (≥ 40 reference roof faces, up to 73). Flat roofs include two single-face
buildings — the degenerate case where plane detection must not over-segment — and the
complex ones are where the topology solver is expected to struggle.

## Rules

- Every file stays **under 2 MB**; the whole set is ~1.3 MB. A pre-commit hook enforces
  the limit, and `tests/unit/test_fixtures.py` asserts it too.
- These are the only binaries in the repository. Nothing else from `data/` is ever
  committed.
- Regenerating them changes the baseline, so it is **its own pull request** with a
  justification. See `CLAUDE.md`.
