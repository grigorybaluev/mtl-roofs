# Evaluation

**These definitions are frozen.** Changing one, or changing
`tests/fixtures/baseline_metrics.json`, is a pull request that changes *nothing else*
and carries a justification. A PR that alters an algorithm and its yardstick at the
same time cannot be reviewed, because the two effects are not separable. See
`CLAUDE.md`.

Metrics are written here **before** they are implemented, and the implementation in
`src/mtl_roofs/evaluation/` is expected to match this document exactly.

## What is being compared

Reconstruction (from 2015 LiDAR) against the city's 2016 LOD2 CityGML model.

The reference is **not ground truth in the strict sense**. It is an independent
photogrammetric product with its own error, built from different imagery a year
later. Every number below is therefore a *disagreement* between two models, and the
write-up must say so. Where the reference is known to be weak — synthetic walls,
merged building groups — the metric is restricted or the building is excluded and
counted.

## Headline metrics

### 1. Vertical RMSE

Both surfaces are sampled onto a common **0.5 m** grid clipped to the reference
building's footprint, in EPSG:2950, elevations in metres above CGVD28.

$$\mathrm{RMSE} = \sqrt{\frac{1}{|V|}\sum_{c \in V} (z^{\text{pred}}_c - z^{\text{ref}}_c)^2}$$

where $V$ is the set of cells where **both** surfaces are defined.

Cells missing from either surface are excluded — and that exclusion is exactly why
RMSE is never reported without coverage. A reconstruction that emits one small
well-fitted facet and nothing else scores a superb RMSE.

### 2. Coverage

$$\mathrm{coverage} = \frac{|V|}{|\{c : z^{\text{ref}}_c \text{ defined}\}|} \in [0, 1]$$

Reported next to RMSE in every table. **Never report one without the other.**

### 3. Plane orientation error

For each matched pair of roof planes, the unsigned angle between unit normals:

$$\theta = \arccos\left(\left|\hat{n}^{\text{pred}} \cdot \hat{n}^{\text{ref}}\right|\right) \in [0°, 90°]$$

Unsigned because a roof plane's outward orientation is a convention of whichever
writer produced it. Planes are matched greedily by smallest angle among planes whose
projected areas overlap by IoU ≥ 0.3. Report the **median** and the 90th percentile;
the mean is dominated by a handful of catastrophic faces and hides the typical case.

### 4. Face-count / topology agreement

$$\mathrm{agreement} = \frac{\min(f^{\text{pred}}, f^{\text{ref}})}{\max(f^{\text{pred}}, f^{\text{ref}})} \in [0, 1]$$

Symmetric, so over- and under-segmentation by the same factor score alike. Only
roof faces count; the reference's walls and ground are synthetic.

Reported alongside the raw counts, because agreement alone cannot distinguish "both
found 4 faces" from "both found 40".

### 5. Building match rate

The fraction of footprints matched to a reference building at IoU ≥ 0.5. Because the
footprint layer carries **no building identifier**, matching is spatial and
imperfect; the match rate is a headline number, not a footnote.

## Breakdowns

Every headline metric is broken down by:

- **roof type** — `flat` / `mixed` / `pitched`, defined geometrically from the
  reference model as the area-weighted fraction of roof steeper than 15°:
  `< 0.10` flat, `< 0.60` mixed, otherwise pitched. This is the same rule the v1
  classifier is scored against, so it must stay reproducible;
- **building size** — footprint area `<100`, `100–250`, `250–1000`, `≥1000` m²;
- **point density** — `<5`, `5–10`, `10–20`, `≥20` points/m².

Bins are fixed constants in `evaluation/report.py`, not derived from the run, so
that two runs are comparable.

## Known error sources

Ranked by expected impact.

1. **Temporal mismatch (largest).** LiDAR is Nov–Dec 2015; the reference is 2016.
   Buildings built, demolished, extended or re-roofed between the two produce large
   errors that are not reconstruction errors. Flagged by a robust height difference
   over the whole footprint and reported separately.
2. **Synthetic reference walls.** The reference extrapolates walls from roof edges to
   3 m below grade. Only roof surfaces are measured, so **only roofs are scored.**
3. **Merged building groups.** Reference buildings with ids like `Groupe9637915` are
   merged blocks that cannot be matched one-to-one against a footprint. Counted and
   excluded from per-building metrics.
4. **Footprints partly derived from the same LiDAR.** Some `CARTO-BAT-TOIT` features
   have `source = LiDAR aérien 2015`; for those, the footprint is not independent of
   this project's input. Flagged from the layer's own `source` attribute and reported
   separately.
5. **Vertical datum.** Both datasets are CGVD28. No vertical transformation is
   applied, and none should be: introducing CGVD2013 on one side only would add a
   systematic metre-level bias.
6. **CRS.** EPSG:2950, *not* 32188. The difference is decimetre-level — invisible in
   a map, fatal in a centimetre-level RMSE.
7. **Footprint accuracy.** `EQM_plani` is ±30–40 cm; `EQM_alti` is not determined.
   Clipping errors at footprint edges are therefore expected and are why edge cells
   are eroded by one grid cell before sampling.
8. **Point density variation.** 18 pts/m² is an island-wide average for one tile;
   occlusion behind tall buildings and on steep north faces gives locally far less.
   This is why density is a breakdown axis.

## The failure log

Every building produces a row, including failures — a building that could not be
reconstructed must be *visible*, not absent. Statuses:

| status | meaning |
|---|---|
| `ok` | reconstructed and matched |
| `no-match` | no reference building at IoU ≥ 0.5 |
| `too-few-points` | below the minimum point count for plane fitting |
| `no-planes` | RANSAC found no plane meeting the inlier threshold |
| `solver-failed` | topology solver did not converge |
| `not-watertight` | extrusion produced a non-closed solid |
| `grouped-reference` | reference is a merged block |
| `suspect-temporal` | large uniform height offset; probably changed since 2015 |

The **failure gallery** shows the worst *N* buildings by RMSE with a diagnosis each.
A v0 report that only shows successes is not finished.

## Regression guard

`eval-smoke` runs the pipeline over `tests/fixtures/` on every PR touching `src/**`,
compares against `tests/fixtures/baseline_metrics.json` and fails if a headline
metric regresses beyond the tolerance stored in that file. Tolerances exist because
RANSAC is stochastic; runs are seeded, but seeds do not survive refactors.
