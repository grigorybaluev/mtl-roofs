# 0005. Reconstruct per footprint, score per reference building

- **Status:** accepted
- **Date:** 2026-09-23
- **Supersedes:** [0004](0004-building-matching.md)

## Context

ADR 0004 matched footprints to reference buildings one to one, at IoU ≥ 0.5. It
assumed both datasets split the city into buildings the same way. Measured on study
area `cdn-ndg-03` (1,108 non-grouped reference buildings, 788 `CARTO-BAT-TOIT`
polygons touching them), they don't:

| | |
|---|---:|
| footprints holding exactly one reference building | 441 |
| footprints holding two (semi-detached pairs) | 311 |
| footprints holding three or more (rows; the largest holds 15) | 9 |
| reference buildings that share a footprint with another | **60%** |
| reference buildings with a best IoU ≥ 0.5 | 68% |
| reference buildings with a best IoU in [0.4, 0.6) | **51%** |
| reference buildings more than 50% inside a single footprint | **100%** |

`CARTO-BAT-TOIT` draws one polygon per continuous roof. The reference model splits
that roof into one building per address. For a semi-detached pair the IoU is about
0.5, and planimetric noise decides which side of the threshold it lands on. Under
0004, about a third of buildings would be reported `no-match`, and the match rate
would measure this difference between the datasets rather than the reconstruction.

Containment has no such ambiguity: every reference building lies mostly inside
exactly one footprint.

The footprint is the only building outline the pipeline may use as input. Splitting
it along reference boundaries would feed the reference into the reconstruction, and
the evaluation would then partly measure the reference against itself.

## Options

### A. Keep 0004 (one to one, IoU ≥ 0.5)
**Pros:** already written; simple. **Cons:** about 32% of buildings drop out as
`no-match`, and half the rest depend on noise around the threshold. The headline match
rate would describe how the two datasets split buildings.

### B. Score per footprint
Merge the reference buildings inside each footprint into one unit.
**Pros:** simple; the reconstruction and scoring units are the same.
**Cons:** results come per roof block rather than per building. A bad half of a pair
can hide behind a good half, and the roof-type and size breakdowns blur.

### C. Reconstruct per footprint, score per reference building
**Pros:** the input stays independent of the reference. Assignment is unambiguous
(100% here), and metrics stay per building, as the breakdowns assume.
**Cons:** one reconstructed roof face can span two reference buildings and is then
scored in both. Needs a stable id for footprints, which have none.

## Decision

**Option C.** It's the only option that keeps the input independent of the reference
and still reports per building.

- **Unit of reconstruction:** one `CARTO-BAT-TOIT` polygon. Its id is
  `fp-<E>-<N>`: the polygon's centroid in EPSG:2950, in decimetres, rounded half up
  to integers (e.g. `fp-2929762-50357646`). It depends only on the source geometry, so
  it is identical on every run. This is the project's building id, carried through
  reconstruction, metrics and the tileset batch table. Across all 237,810 polygons the
  id collides 10 times, and every collision is the same polygon recorded twice with
  different attributes (symmetric difference < 0.004 m²). Records sharing an id are
  therefore **one footprint**. It is flagged LiDAR-derived if any of its records is.
- **Assignment:** each reference building goes to the footprint that contains the
  largest share of its 2D roof outline, if that share is **≥ 0.5**. Ties go to the
  lower footprint id. Found with an STRtree over footprint envelopes.
- **Scoring:** each assigned reference building is scored against its footprint's
  reconstruction, **restricted to the reference building's outline** (definitions in
  `docs/evaluation.md`).
- `Groupe*` reference buildings stay excluded and counted, as under 0004.

## Consequences

- The match rate becomes "share of reference buildings assigned to a footprint". It
  is expected to be close to 1 here, so it's no longer where the interesting failures
  show up. Footprints with no reference building are counted separately
  (`no-reference`): new builds, sheds, or footprint-only structures.
- Fixtures are cut per footprint: points clipped to the whole footprint, with every
  reference building it holds. A fixture cut to one reference building would give the
  reconstruction half a roof.
- A face spanning a party wall is scored in both buildings. That's what the per-building
  RMSE grid does anyway; it counts for face-count agreement too, and
  `docs/evaluation.md` says so.
- **Revisit if** another study area shows reference buildings split across footprints
  (share < 0.5 in any footprint), which containment can't handle.
