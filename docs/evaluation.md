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

**Units** ([ADR 0005](adr/0005-reconstruct-per-footprint-score-per-building.md)): the
pipeline reconstructs one roof per `CARTO-BAT-TOIT` footprint. Scoring is **per
reference building**. Each one is assigned to the footprint containing the largest
share (≥ 0.5) of its *roof outline*: the 2D union of its roof surfaces. It is then
scored against that footprint's reconstruction, restricted to the outline. One
footprint often holds a semi-detached pair or a row, so it can be scored against
several reference buildings.

The reference is **not ground truth in the strict sense**. It is an independent
photogrammetric product with its own error, built from different imagery a year
later. Every number below is therefore a *disagreement* between two models, and the
write-up must say so. Where the reference is known to be weak — synthetic walls,
merged building groups — the metric is restricted or the building is excluded and
counted.

## Headline metrics

### 1. Vertical RMSE

Both surfaces are sampled onto a common **0.5 m** grid clipped to the reference
building's roof outline, in EPSG:2950, elevations in metres above CGVD28. The
predicted surface is the reconstruction of the footprint the building is assigned to.

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
writer produced it. Predicted faces are first clipped to the reference building's
roof outline. Planes are matched greedily by smallest angle among planes whose
projected areas overlap by IoU ≥ 0.3. Report the **median** and the 90th percentile;
the mean is dominated by a handful of catastrophic faces and hides the typical case.

### 4. Face-count / topology agreement

$$\mathrm{agreement} = \frac{\min(f^{\text{pred}}, f^{\text{ref}})}{\max(f^{\text{pred}}, f^{\text{ref}})} \in [0, 1]$$

Symmetric, so over- and under-segmentation by the same factor score alike. Only
roof faces count; the reference's walls and ground are synthetic.

$f^{\text{pred}}$ counts the faces of the assigned footprint's reconstruction with
**at least 25% of their projected area** inside the reference building's roof outline.
A face spanning a party wall therefore counts for both buildings. A sliver of the
neighbour's face, caused by the footprints' ±30–40 cm edge accuracy, counts for
neither.

Reported alongside the raw counts, because agreement alone cannot distinguish "both
found 4 faces" from "both found 40".

### 5. Building match rate

The fraction of non-grouped reference buildings assigned to a footprint, i.e. with
**≥ 50% of their roof outline inside one footprint** (ADR 0005). The footprint layer
carries **no building identifier**, so assignment is spatial. The match rate is a
headline number, not a footnote.

Reported next to it: the number of footprints with no reference building assigned
(`no-reference`), and the distribution of reference buildings per footprint.

*Superseded definition:* one-to-one matching at IoU ≥ 0.5 (ADR 0004). On `cdn-ndg-03`
it would have left 32% of buildings unmatched, because 60% of reference buildings
share a footprint with a neighbour.

## Breakdowns

Every headline metric is broken down by:

- **roof type** — `flat` / `mixed` / `pitched`, defined geometrically from the
  reference model as the area-weighted fraction of roof steeper than 15°:
  `< 0.10` flat, `< 0.60` mixed, otherwise pitched. This is the same rule the v1
  classifier is scored against, so it must stay reproducible;
- **building size** — reference roof-outline area `<100`, `100–250`, `250–1000`,
  `≥1000` m²;
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

Every reference building and every footprint produces a row, including failures. A
building that could not be reconstructed must be *visible*, not absent. A reference
building takes the status of its footprint's reconstruction unless it has one of its
own (`no-match`, `grouped-reference`). Statuses:

| status | meaning |
|---|---|
| `ok` | reconstructed and matched |
| `no-match` | reference building with no footprint containing ≥ 50% of its roof outline |
| `no-reference` | footprint with no reference building assigned (reported, not scored) |
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
