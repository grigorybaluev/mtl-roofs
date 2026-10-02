# Contributing

This is a solo portfolio project, but it is run like a maintained one: everything is
tracked in an issue, every change lands through a pull request, and `main` is gated by
CI. Issues and pull requests from others are welcome.

## Getting set up

```bash
git clone https://github.com/grigorybaluev/mtl-roofs && cd mtl-roofs
cp .env.example .env
pixi install
pixi run pre-commit install
pixi run test
```

You need [pixi](https://pixi.sh) and Docker. You do **not** need Homebrew, a local
PostgreSQL, or a system GDAL — see [`CLAUDE.md`](CLAUDE.md) for why.

## The loop

1. Take the highest-priority open issue in the current
   [milestone](https://github.com/grigorybaluev/mtl-roofs/milestones) that is not labelled
   `blocked` or `needs-decision`, or open one. Issues use forms; pick the right type.
2. Branch: `<type>/<issue-number>-<slug>` — `feat/14-ransac-plane-detection`.
3. Commit with [Conventional Commits](https://www.conventionalcommits.org/).
4. Open a PR with `Closes #<n>` and fill in the template.
5. Green CI, then squash merge.

## What a PR needs

- Tests for anything that is not a stub. A property test is worth three examples.
- `pixi run lint`, `pixi run typecheck` and `pixi run test` passing locally.
- The **evaluation impact** table if you touched `geometry/`, `evaluation/` or `ml/`.
- A screenshot for any viewer change.
- A conventional-commit PR **title** — squash merge uses it as the commit message.

## Things that will get a PR sent back

- **Committed data.** `data/` is ignored; fixtures live in `tests/fixtures/` and are
  each under 2 MB.
- **Changing a metric definition or the baseline alongside an algorithm change.**
  These are separate PRs. CI enforces it. See [`docs/evaluation.md`](docs/evaluation.md).
- **Implementing past an open ADR.** If a decision has real alternatives, write the
  ADR first and label the issue `needs-decision`.
- **Asserting facts about the source data** that were not checked against the source.

## Experiments

ML and algorithm work uses the **experiment** issue form: hypothesis, method, split,
metrics, and the baseline to beat, all written down *before* the run. Fill in the
result when you close it — a negative result is still a result, and it is the one most
worth recording.
