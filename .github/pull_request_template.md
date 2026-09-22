## Summary

<!-- What changed and why. One paragraph. -->

Closes #

## Type of change

- [ ] `feat` — new capability
- [ ] `fix` — bug fix
- [ ] `experiment` — ML / algorithm experiment (fill in the result on the issue)
- [ ] `data` — new or changed data source (`data/manifest.yaml` updated)
- [ ] `docs`
- [ ] `chore` / `refactor` / `perf`

## How it was tested

<!-- Commands run, fixtures used, what you checked by hand. -->

## Evaluation impact

<!-- REQUIRED if this touches src/mtl_roofs/geometry, /evaluation or /ml.
     Paste the eval-smoke metric table, or state why the metrics cannot move. -->

| metric | baseline | this PR | Δ |
|---|---:|---:|---:|
| vertical RMSE (m) | | | |
| orientation error, median (°) | | | |
| coverage | | | |
| buildings reconstructed | | | |

## Screenshots

<!-- REQUIRED for viewer changes. Before / after. -->

## Checklist

- [ ] Tests added or updated, and `pixi run test` passes
- [ ] `pixi run lint` and `pixi run typecheck` pass
- [ ] Docs updated (README / `docs/` / docstrings)
- [ ] PR title is a conventional commit — it becomes the squash commit message
- [ ] **No data committed.** Fixtures only, each under 2 MB
- [ ] Metric definitions and `baseline_metrics.json` are **not** changed in this PR
      alongside algorithm changes (they get their own PR)
- [ ] A design decision with real alternatives has an ADR in `docs/adr/`
