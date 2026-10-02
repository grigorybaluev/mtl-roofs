# 0006. Refine roof planes by soft LM with gated regularity priors

- **Status:** accepted
- **Date:** 2026-10-02

## Context

Detected planes are fitted to each face's points independently, and #19 is to refine
them jointly. Measured on the 20 fixtures at seed 0 (89 planes, 112 adjacent pairs;
details in [`docs/topology-solver.md`](../topology-solver.md)):

- Ridges are already level: on opposed pairs the intersection line is within 0.19°
  of horizontal (median), and the two slopes differ by 0.44°.
- Only 4 places have four or more mutually adjacent planes, which is where corner
  inconsistency can arise. There are 47 three-plane corners, and three planes always
  meet at a point.
- 17 of the 112 adjacent pairs are within 10° of parallel, almost all flat sections
  at different heights. Intersecting them is ill-posed: a vertical step joins them.
- Against the majority reference face, the median orientation error is **2.04°**.
  - Snapping azimuths to footprint edge directions gives 1.91°.
  - Also levelling faces under 5° gives **1.39°**.
- The reference models flat roofs as exactly level: 219 of its 223 faces under 5° are
  below 0.1°. The LiDAR sees them at about 1°, their drainage slope.

The evaluation reports orientation error and vertical RMSE against that reference.

## Options

### A. Soft LM over plane parameters with gated regularity priors
Point evidence plus priors: level, aligned with the footprint, level ridge, symmetric
slopes. Plus a consistency term for corners of four or more planes. Each prior applies
only where the fitted planes already satisfy it within a few degrees.
**Pros:** uses where the measured gain is (levelling, alignment). Conflicting priors
settle at a weighted compromise rather than an arbitrary order of snaps. Corners come
out consistent, which the extrusion needs. It's what #19 specified.
**Cons:** the most machinery; weights and gates to justify.

### B. Soft LM with geometric consistency only, no priors
**Pros:** nothing about "ideal roofs" is assumed; the planes are what the points say.
**Cons:** on the fixtures, consistency has little to fix: ridges are already level, and
there are only 4 over-determined corners. Almost none of the measured disagreement is
addressed.

### C. Rule-based snapping, no solver
**Pros:** simplest. Snapping alone produced the 1.39° above.
**Cons:** the order of snaps decides conflicts. A plane snapped to two relations can
satisfy only the last, and corners stay inconsistent.

## Decision

**Option A**, confirmed by the maintainer on 2026-10-02. The gain measured on the
fixtures is in what the priors assert, and only a joint solve can satisfy several of
them and the corners at once.

Solver failures are recorded as the single failure-log status `solver-failed` with a
`reason` (`no-convergence`, `ill-conditioned`). Conditions that don't make the planes
unusable are per-building warnings (`sliver-face`, `ill-conditioned-corner`,
`prior-conflict`), not statuses. Also confirmed on 2026-10-02. Definitions are in
`docs/topology-solver.md`; the failure-log fields are in `docs/evaluation.md`.

## Consequences

- **Part of the gain is agreement with a convention.** Levelling flat roofs moves the
  reconstruction away from the physical roof, which drains at about 1°, and towards
  how the reference and LOD2 practice model it. The orientation error on flat roofs
  therefore partly measures that shared convention. The write-up must say this, and
  `level_gate_deg` = 0 turns the prior off for an ablation that reports both.
- The priors use only the points and the footprint, never the reference, so the
  reconstruction stays independent of what it is scored against (ADR 0005).
- Gates keep a real oblique face oblique, but a face that really is skewed by less than
  the gate (5° azimuth, 3° slope) is snapped. That's the price of the prior, and
  `prior-conflict` reports the cases where it fought the evidence.
- Default gates and weights are set from physical reasoning and the measurement above,
  which uses the same 20 fixtures eval-smoke scores. They must not be tuned further
  against eval-smoke; a sweep belongs to a `type:experiment` issue on buildings outside
  the fixtures.
- **Revisit if** a study area with many non-orthogonal or curved roofs shows the gates
  snapping faces they shouldn't (a high `prior-conflict` rate), or if the v1 ML stage
  supplies roof-type labels that could select priors per roof class.
