# 0004. Matching reconstructions to reference buildings

- **Status:** superseded by [0005](0005-reconstruct-per-footprint-score-per-building.md)
- **Date:** 2026-09-22

## Context

Evaluation requires pairing each reconstructed building with its counterpart in the
reference model. Verified from the data:

- `CARTO-BAT-TOIT` (237,810 roof polygons) carries **no identifier field at all** —
  its attributes are `calque, projection, reference, EQM_plani, EQM_alti, MAJ,
  methode, source, producteur, superficie, version`;
- the CityGML model identifies buildings by `gml:id`, either a numeric city id
  (`1585788`) or a merged block (`Groupe9637915`);
- there is consequently **no join key** between the two.

The footprint layer's stated planimetric accuracy is ±30–40 cm, and the two products
were captured a year apart, so geometry does not agree exactly either.

## Options

### A. Centroid containment
Match a footprint to the reference building containing its centroid.
**Pros:** trivial, fast.
**Cons:** fails for L-shaped and courtyard buildings whose centroid falls outside the
polygon; silently mismatches in dense row housing, which is most of the study area.

### B. Maximum IoU above a threshold
**Pros:** robust to small planimetric offsets; the IoU value is itself a quality
signal; the threshold makes non-matches explicit rather than forcing a bad pair.
**Cons:** O(n²) without an index; needs a tie-break rule; one-to-many cases
(merged groups) must be handled explicitly.

### C. Match via the `parcelle` generic attribute
**Pros:** would be a real key.
**Cons:** `parcelle` is a property-roll reference, not a building id; one parcel holds
several buildings and one building can straddle parcels. It does not identify a building.

## Decision

**Option B.** Candidates are found with an STRtree index on footprint envelopes, paired
by maximum IoU, and accepted at **IoU ≥ 0.5**. Ties are broken by smaller centroid
distance, then by lower `gml:id` so the result is deterministic.

Reference buildings whose id begins with `Groupe` are excluded from per-building
metrics and counted separately: they are merged blocks with no one-to-one counterpart.

**The match rate is a reported headline metric**, not an implementation detail. A
pipeline that quietly drops the buildings it cannot match would flatter itself.

## Consequences

Some buildings will not match, and that is a legitimate result to publish. The 0.5
threshold is arbitrary-but-conventional; the report includes the IoU distribution so a
reader can judge it. Changing the threshold changes the metrics, so it is frozen under
the same rule as the metric definitions in `docs/evaluation.md`.
