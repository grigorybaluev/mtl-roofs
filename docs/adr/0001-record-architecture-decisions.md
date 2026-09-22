# 0001. Record architecture decisions

- **Status:** accepted
- **Date:** 2026-09-22

## Context

This is a solo portfolio project whose value is largely in *why* things were built a
certain way. Reconstruction has many defensible designs; a reader evaluating the work
needs the reasoning, not just the result. Left unrecorded, the reasoning is gone
within weeks and the code looks arbitrary.

## Options

### A. No ADRs; explain in commit messages and code comments
**Pros:** nothing extra to maintain.
**Cons:** rationale gets scattered and is lost to squash merges; alternatives
considered and rejected leave no trace at all.

### B. Lightweight ADRs in `docs/adr/`
**Pros:** one file per decision, reviewable in the PR that makes the decision,
greppable, and links from the issue that raised it.
**Cons:** a small ongoing discipline cost.

## Decision

Option B, in the Nygard style, numbered sequentially from `template.md`.

A decision needs an ADR when there is a real alternative someone could reasonably
prefer. Routine choices do not. Until the ADR is accepted, the driving issue carries
the `needs-decision` label and does not get implemented.

## Consequences

Every non-obvious design choice is defensible from the repository alone. ADRs are
immutable once accepted: a change of mind is a new ADR superseding the old one, so
the history of the thinking survives.
