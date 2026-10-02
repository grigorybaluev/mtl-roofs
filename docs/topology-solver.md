# Topology solver

How `geometry/topology.py` refines one footprint's detected roof planes (#19). The
choices behind it are in [ADR 0006](adr/0006-topology-solver-priors.md). This
document is the specification; the code is expected to match it.

## Input and output

**Input:** the planes from `detect_planes()`, each with its inlier points, and the
footprint polygon. Nothing from the reference model is used.

**Output:** the same planes, in the same order with the same inliers, with refined
parameters. Also:

- the relations found between adjacent planes (ridge, hip/valley, step, other) and the
  corners where four or more planes meet, which the extrusion (#20) builds faces from;
- a status, `ok` or `solver-failed`; a failure also carries a `reason`;
- warnings, which don't fail the building;
- diagnostics: iterations, initial and final cost, gradient norm, and per plane the
  rotation applied and the point RMS before and after.

## What was measured first

On the 20 fixtures at seed 0 (89 planes, 112 adjacent pairs; *adjacent* means inliers
within 0.75 m of each other in plan):

| | detected | reference |
|---|---:|---:|
| ridges (opposed pairs): ridge tilt, median | 0.19° | 0.10° |
| ridges: slope difference, median | 0.44° | 1.74° |
| sloped faces: downslope azimuth off the footprint's axes, median / p90 | 0.48° / 2.22° | 0.29° / 2.11° |
| faces under 5°: share at exactly 0° (< 0.1°) | 0 of 15 | 219 of 223 |
| adjacent pairs < 10° apart | 17 of 112 | — |
| corners where ≥ 4 planes are mutually adjacent | 4 | — |

Orientation error against the majority reference face (44 detected planes with at least
half of their points inside one reference face):

| | median | p90 |
|---|---:|---:|
| detected planes, as fitted | 2.04° | 6.48° |
| azimuth snapped to the nearest footprint edge direction (≤ 10°) | 1.91° | 6.41° |
| azimuth snapped to footprint axes, and faces ≤ 5° set level | 1.39° | 6.41° |

Most of the gain comes from levelling flat roofs. The LiDAR sees them at about 1°,
which is their drainage slope; the reference models them at exactly 0°. ADR 0006
discusses what that means for the measurement.

The 17 near-parallel adjacent pairs are almost all flat sections at different heights.
They don't meet along a line: a vertical step joins them.

## Unknowns and parameterisation

All coordinates are first shifted to a local origin: the centroid of the footprint's
points. EPSG:2950 northings are about 5 × 10⁶ m. A plane offset expressed at that
distance couples every rotation to a lever arm five million metres long, and the normal
equations lose most of their precision to it.

Plane *i* has normal $\hat n_i$ with $|\hat n_i| = 1$ and passes through
$\hat n_i \cdot (x - c_i) = t_i$, where $c_i$ is the centroid of its inliers. Rotating
the normal therefore pivots the plane about its own centroid rather than the origin,
which decouples orientation from offset in the fit. At the fitted plane $t_i = 0$.

The normal is parameterised in a **tangent basis** at the fitted normal $\hat n_i^0$.
With $u_i, v_i$ orthonormal and both perpendicular to $\hat n_i^0$:

$$\hat n_i(a_i, b_i) = \frac{\hat n_i^0 + a_i u_i + b_i v_i}{\lVert \hat n_i^0 + a_i u_i + b_i v_i \rVert}$$

Each plane thus has exactly three unknowns $(a_i, b_i, t_i)$, matching its three
degrees of freedom. A corner where *k* ≥ 4 planes meet adds its 3D point $p_k$ as three
more unknowns.

**Why a tangent basis rather than a penalty.** The alternative carries $n_i$ as three
free components and adds $w(\lVert n_i\rVert^2 - 1)^2$ to the cost. That leaves a fourth
parameter per plane, the length of $n_i$. Nothing in the data constrains it, and only the
penalty does. The choice of $w$ then trades conditioning against accuracy: a small $w$
leaves the normal equations near-singular along the radial direction, and a large $w$
makes them stiff and the unit constraint dominate the step. In the tangent chart the
constraint holds by construction, so it costs nothing and needs no weight. The Jacobian
is full rank whenever the evidence and the priors determine the plane.

The chart is fixed at the fitted normal rather than re-centred each iteration. Priors
are only applied within a few degrees of the fitted plane (below), so the solver never
rotates a normal far. Over that range the chart's distortion
($\tan\theta$ against $\theta$) is under 0.1%. In return, the data term below is
exactly quadratic in $(a_i, b_i, t_i)$.

## Residuals

The cost is $F = \tfrac12 \lVert r \rVert^2$. Every residual is divided by its standard
deviation, so all terms are dimensionless and comparable.

### Point evidence

Summing squared point-to-plane distances over *N* inliers gives a fit term with *N*
residuals. The LiDAR's formal precision then claims a normal known to about 0.03°
(σ = 5 cm over a few hundred points spread over 10 m). The measurement above shows the
real disagreement with an idealised roof is about 2°. Roofing texture, edge effects and
inlier selection are systematic errors, and adding points doesn't average them away.

The evidence for plane *i* therefore enters as its fit, with an inflated covariance.
Let $S_i$ be the scatter matrix of its inliers about $c_i$, $T_i = [u_i\ v_i]$, and
$\sigma_{p,i}$ the RMS orthogonal residual of its inliers (floored at 2 cm). The
statistical information on the tangent coordinates and the offset is

$$H_i = \frac{1}{\sigma_{p,i}^2}\begin{pmatrix} T_i^\top S_i T_i & 0 \\ 0 & N_i \end{pmatrix}$$

The orientation and offset blocks decouple because the offset is defined at the
centroid. A systematic floor is added in covariance, $C_i = H_i^{-1} + \Sigma_{\text{sys}}$
with $\Sigma_{\text{sys}} = \mathrm{diag}(\sigma_\theta^2, \sigma_\theta^2, \sigma_t^2)$.
That gives $H^{\text{eff}}_i = H_i (I + \Sigma_{\text{sys}} H_i)^{-1}$, which stays
finite when $H_i$ is singular. The residual is

$$r^{\text{fit}}_i = (H^{\text{eff}}_i)^{1/2} \,(a_i,\ b_i,\ t_i)^\top$$

Its square is the Mahalanobis distance from the fitted plane.

### Priors

A prior is a statement about the ideal roof. Each one is applied to a plane or a pair
only if the **fitted** planes already satisfy it to within a gate. The gate is decided
once, before solving, so the cost function doesn't change during the solve. A plane the
data puts outside a gate is left alone: a 20°-skewed bay is a 20°-skewed bay.

| prior | applies to | gate | residual |
|---|---|---|---|
| level | a plane with slope ≤ `level_gate_deg` | 3° | $(n_x, n_y) / \sigma_{\text{prior}}$ (sine of slope, per axis) |
| aligned | a sloped plane whose downslope azimuth is within `azimuth_gate_deg` of a footprint edge direction or its perpendicular | 5° | $\sin\Delta\phi / \sigma_{\text{prior}}$, $\Delta\phi$ from the nearest such direction |
| level ridge | an opposed pair (azimuths 150–210° apart) whose intersection line is within `ridge_gate_deg` of horizontal | 3° | $(n_i \times n_j)_z / \lVert n_i \times n_j \rVert / \sigma_{\text{prior}}$ |
| symmetric | an opposed pair whose slopes differ by at most `symmetry_gate_deg` | 3° | $(\theta_i - \theta_j)/\sigma_{\text{prior}}$, slopes in radians |

Edge directions come from footprint edges at least `min_edge_m` long (2 m), so a
non-rectangular lot offers its own angles rather than one forced orthogonal frame.
$\sigma_{\text{prior}}$ is 0.1°, ten times tighter than the evidence floor
$\sigma_\theta$ = 1°. A gated prior therefore wins against a plane's own fit, but stays
soft enough that two priors pulling one plane in different directions settle at a
weighted compromise, not an arbitrary one.

### Corners

A corner is a set of four or more planes around one point: each is adjacent to at least
two others in the set. (Opposite faces of a pyramid meet only at the apex, so they are
not adjacent themselves.) For each three of them, the planes intersect at a point, provided they're well
conditioned ($\lvert\det[\hat n_a\ \hat n_b\ \hat n_c]\rvert \geq$ `min_corner_det`, 0.1).
These triple points must lie within `corner_tol_m` (0.5 m) of each other. Their mean
must lie within the footprint buffered by that distance, and within `corner_tol_m` in
plan of every incident plane's inliers. Each corner gets an unknown point $p_k$ and one
residual per incident plane:

$$r^{\text{corner}}_{ik} = \frac{\hat n_i \cdot (p_k - c_i) - t_i}{\sigma_{\text{corner}}}, \qquad \sigma_{\text{corner}} = 1\ \text{cm}$$

Three planes always meet at a point, so a three-plane corner needs no residual. With
four or more, the extra planes are what "three roof faces that should share one ridge
point define three slightly different intersections" means; for a hip roof that is the
apex.

## Conditioning

- **Coordinates.** Local origin and centroid pivots, above.
- **Nearly parallel adjacent planes.** Two planes at angle $\alpha$ intersect along a
  line whose position moves by $\delta / \sin\alpha$ for an offset error $\delta$. At 2°,
  a 5 cm offset error moves the intersection by 1.4 m. Adjacent pairs closer than
  `step_angle_deg` (10°) are therefore classified as **steps**. They get no ridge prior
  and take part in no corner, and the extrusion joins them with a vertical face. This is
  15% of adjacent pairs on the fixtures, so it's a common case, not an edge case.
- **Ill-conditioned triples.** A triple below `min_corner_det` is excluded from corner
  detection and recorded as an `ill-conditioned-corner` warning.
- **Slivers.** A face whose inliers are narrow across one direction constrains its
  normal poorly about that direction: one eigenvalue of $T_i^\top S_i T_i$ is small.
  When the width across the face, $\sqrt{12\,\lambda_{\min}/N_i}$, is under
  `sliver_width_m` (0.5 m), the face gets a `sliver-face` warning. Its orientation is
  then determined mostly by the priors, if any apply.
- **Scale.** The damping below is scaled by the diagonal of $J^\top J$ (Marquardt), so
  tangent coordinates (radians), offsets (metres) and corner points (metres) need no
  manual scaling.

## Levenberg–Marquardt

Each iteration solves

$$(J^\top J + \lambda\, \mathrm{diag}(J^\top J))\, \delta = -J^\top r$$

and computes the gain ratio $\rho$ = (actual decrease in $F$) / (decrease predicted by
the linear model).

- **Damping (Nielsen).** Start at $\lambda_0 = \tau \max \mathrm{diag}(J^\top J)$, with
  `lm_tau` = 10⁻³. If $\rho > 0$, accept the step and set
  $\lambda \leftarrow \lambda \max(\tfrac13, 1 - (2\rho - 1)^3)$, $\nu \leftarrow 2$.
  Otherwise reject it and set $\lambda \leftarrow \lambda\nu$, $\nu \leftarrow 2\nu$.
  Small $\lambda$ is Gauss–Newton, which converges quadratically near the solution.
  Large $\lambda$ is short gradient steps, which keep a near-singular system from taking
  a huge one.
- **Convergence**, whichever comes first:
  - $\lVert J^\top r \rVert_\infty \leq$ `gtol` (10⁻⁸);
  - $\lVert \delta \rVert \leq$ `xtol` $(\lVert x \rVert +$ `xtol`$)$, with `xtol` = 10⁻¹⁰.
- **Non-convergence:** `max_iterations` (100) reached first.

All of these are fields of `TopologyParams`, overridable per study area under
`topology:` in `configs/areas/*.yaml`, as `planes:` is.

The Jacobian is analytic. Every residual is a function of the normals, offsets and
corner points, and $\partial \hat n_i / \partial(a_i, b_i)$ follows from the chart.

## Failure modes

**`solver-failed`**, with a `reason`. The planes are not usable as solved.

| reason | condition | recorded |
|---|---|---|
| `no-convergence` | `max_iterations` reached without meeting a convergence criterion | final cost, gradient norm, iterations |
| `ill-conditioned` | at the solution, the reciprocal condition number of the undamped $J^\top J$ is below `rcond_min` (10⁻¹²): some parameter is set by the damping, not by evidence or priors | the reciprocal condition number |

**Warnings.** The building stays `ok`, and the warning is listed with it.

| warning | condition |
|---|---|
| `sliver-face` | a face under `sliver_width_m` across (above) |
| `ill-conditioned-corner` | a triple of mutually adjacent planes below `min_corner_det` |
| `prior-conflict` | a plane ends more than `conflict_sigma` (5) evidence standard deviations from its fit, $\lVert r^{\text{fit}}_i \rVert > 5$: the priors and corners overrode its evidence |

The module docstring listed four failure modes before this was written. Where each one
went:

- **Nearly parallel adjacent faces** are classified as steps, which is what they are on
  the fixtures, not failures.
- **Fewer than three faces at a corner** happens only on the footprint boundary, where
  the wall plane is the third face. That corner belongs to the extrusion (#20), not the
  solver.
- **Thin slivers** become the `sliver-face` warning.
- **Curved or domed roofs** can't be detected from planes alone. A dome reaches the
  solver as many small planes, each fitting its inliers well. It shows in the
  evaluation as low face-count agreement and coverage, which is where it is diagnosed.

## Tests the implementation must pass

- A synthetic gable and a hip roof, sampled exactly, are returned exactly
  (to 10⁻⁹): the priors and corners are consistent with exact geometry, so they must
  not move it.
- The same roofs with 3 cm noise and a few degrees of skew converge to the known
  geometry: azimuths on the footprint axes, equal slopes, a level ridge, and on the hip
  all four faces through one apex point (to 1 mm), within the noise of the true one.
- A face outside every gate keeps its fitted orientation.
- Each failure reason and warning above is produced by a constructed case.
- All 20 fixtures run without an unhandled exception.
