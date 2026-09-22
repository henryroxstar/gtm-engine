---
source: manual
refreshed: 2026-09-21
review: evergreen
---

# ICP & personas — EXPERIMENT OVERLAY (sample)

> This file replaces `knowledge/icp-personas.md` for the length of a run started with
> `--overlay _sample`. It is a **fictional** worked example, not a tenant's ICP.

Include a file in an overlay **only if it differs**. Everything you leave out falls through
to the product level, then to the profile — so an experiment that changes the scoring
rubric and nothing else is a directory with `EXPERIMENT.toml` and `icp-scoring.toml` in it.

## What to change here

The point of overriding this file is to describe a cohort you are **testing**, not one you
have committed to. Keep the original in `knowledge/` untouched so the comparison has a
control, and so the week's live pipeline is unaffected if the experiment is abandoned.

## Target company profile (sample)

| Attribute | Experimental cut | Live ICP (for contrast) |
|---|---|---|
| Size | 50–200 employees | 1,000+ |
| Buying trigger | hired their first ops lead | published a compliance commitment |
| Why this cut | the smaller band answers a cost argument the enterprise band ignores | — |

## Personas (sample)

- **Ops lead** — owns the workflow being replaced; reads a throughput argument.
- **Owner-operator** — signs; reads a margin argument.

> If an experimental persona is not in the profile's `role-vocabulary.toml`, add a
> `role-vocabulary.toml` to this overlay too. A persona the vocabulary cannot resolve is
> **not** an error — its rows are booked as *unassignable*, so the coverage report reads
> zero and the copy looks like the problem. Check with:
>
> ```
> python -m gtm_core.hook_coverage --profile <tenant> --matrix-only
> ```
